#!/usr/bin/env python3
"""Neural Persian TTS for the packs: VITS/Piper voice + emotion layer.

Why this and not a hosted TTS: the sandbox cannot reach Hugging Face, Edge/Google TTS
or any other cloud API, so the voice has to run locally on CPU. A Piper-compatible
Persian VITS model (espeak-ng "fa" frontend, 22050 Hz mono) is fetched from a GitHub
release and used through onnxruntime. Everything in this file is offline and
deterministic.

Emotion is applied in two stages:
  1. synthesis-time prosody (length_scale = pace, noise_scale = expressiveness,
     noise_w_scale = timing jitter)
  2. post-processing with ffmpeg rubberband: pitch and formant shift independently of
     tempo (anger = lower formants + faster + harder limiter, panic = higher pitch,
     sneer = slight nasal EQ...), plus drive/compression for shouting.

CLI:
    python3 tools/tts_engine.py --setup                 # download the voice (once)
    python3 tools/tts_engine.py --say "متن" --emotion angry -o out.wav
"""

import argparse
import json
import os
import math
import re
import shutil
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, ".cache", "tts", "fa_piper")
CACHE = os.path.join(ROOT, ".cache", "tts")

# The Persian voice that ships inside gyroing/Persian-Piper-TTS-WebAssembly.
# (Piper VITS, espeak-ng fa frontend, single male speaker, 22050 Hz.)
SOURCE_REPO = "gyroing/Persian-Piper-TTS-WebAssembly"


# ---------------------------------------------------------------------------
# emotions: what the dozer feels when he says the line
# ---------------------------------------------------------------------------
# A unit keeps ONE voice. Emotion only changes how the line is delivered (pace,
# expressiveness, air), never the pitch or the formants - those are locked per clip to
# VOICE_PITCH_HZ below, otherwise every line sounds like a different person.
EMOTIONS = {
    "neutral":   dict(length=1.00, noise=0.55, gain=0.0, tone=None),
    "angry":     dict(length=0.93, noise=0.66, gain=1.0, tone="edge"),
    "furious":   dict(length=0.88, noise=0.70, gain=1.8, tone="edge"),
    "tired":     dict(length=1.16, noise=0.48, gain=-1.0, tone="dull"),
    "smug":      dict(length=1.09, noise=0.58, gain=0.0, tone=None),
    "panic":     dict(length=0.84, noise=0.68, gain=1.2, tone="edge"),
    "scream":    dict(length=0.80, noise=0.72, gain=2.4, tone="edge"),
    "muttering": dict(length=1.22, noise=0.45, gain=-1.5, tone="dull"),
    "deadpan":   dict(length=1.05, noise=0.50, gain=0.0, tone=None),
}

# One target for the whole unit: median f0 of every clip is pulled to this value.
VOICE_PITCH_HZ = 92.0   # nearest common point to the model's own register (raw median 96 Hz): least pitch correction
MAX_PITCH_CORRECTION_SEMITONES = 4.0   # beyond this rubberband smears the consonants


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# model setup
# ---------------------------------------------------------------------------
def _extract_from_wasm_data(data_path, js_path, out_dir):
    """Pull model.onnx / tokens.txt / espeak-ng-data out of an Emscripten package."""
    js = open(js_path, "rb").read().decode("latin-1")
    i = js.find("loadPackage({")
    if i < 0:
        raise RuntimeError("no loadPackage() metadata in %s" % js_path)
    blob = js[i + len("loadPackage("):]
    depth, end = 0, None
    for j, ch in enumerate(blob):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    meta = json.loads(blob[:end])
    raw = open(data_path, "rb").read()
    keep = 0
    for f in meta["files"]:
        name = f["filename"]
        if name in ("/model.onnx", "/tokens.txt") or name.startswith("/espeak-ng-data/"):
            dest = os.path.join(out_dir, name.lstrip("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").write(raw[f["start"]:f["end"]])
            keep += 1
    return keep


def setup(force=False):
    """Fetch + unpack the Persian voice. Uses gh (works when raw.githubusercontent is blocked)."""
    onnx = os.path.join(MODEL_DIR, "model.onnx")
    cfg = os.path.join(MODEL_DIR, "model.onnx.json")
    if os.path.isfile(onnx) and os.path.isfile(cfg) and not force:
        print("voice already present at %s" % MODEL_DIR)
        return MODEL_DIR

    os.makedirs(MODEL_DIR, exist_ok=True)
    tmp = os.path.join(CACHE, "_src")
    if not os.path.isdir(os.path.join(tmp, ".git")):
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
        subprocess.run(["git", "clone", "--depth", "1",
                        "https://github.com/%s.git" % SOURCE_REPO, tmp], check=True)

    data = os.path.join(tmp, "sherpa-onnx-wasm-main.data")
    js = os.path.join(tmp, "sherpa-onnx-wasm-main.js")
    if not (os.path.isfile(data) and os.path.isfile(js)):
        raise RuntimeError("unexpected repo layout in %s" % tmp)
    n = _extract_from_wasm_data(data, js, MODEL_DIR)
    print("extracted %d files (model + tokens + espeak data)" % n)

    # espeak data: prefer the copy shipped with espeakng-loader (has the fa_dict)
    try:
        import espeakng_loader
        src = espeakng_loader.get_data_path()
        dst = os.path.join(MODEL_DIR, "espeak-ng-data")
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print("espeak-ng-data: using espeakng-loader copy (complete)")
    except Exception as e:
        print("espeak-ng-data: keeping extracted copy (%s)" % e)

    write_piper_config(onnx, cfg)
    print("voice ready: %s" % MODEL_DIR)
    return MODEL_DIR


def write_piper_config(onnx_path, cfg_path):
    """Rebuild the .onnx.json piper expects from the model's own metadata + tokens."""
    import onnxruntime as ort
    meta = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"]).get_modelmeta().custom_metadata_map
    ids = {}
    for line in open(os.path.join(os.path.dirname(onnx_path), "tokens.txt"), encoding="utf-8"):
        if not line.strip():
            continue
        ch, idx = line.rstrip("\n").rsplit(" ", 1)
        ids[ch] = [int(idx)]
    cfg = {
        "audio": {"sample_rate": int(meta["sample_rate"]), "quality": "medium"},
        "espeak": {"voice": meta["voice"], "voice_uri": meta["voice"],
                   "phonemes": "espeak", "data_dir": os.path.join(os.path.dirname(onnx_path), "espeak-ng-data")},
        "language": {"code": "fa", "name_native": "فارسی", "name_english": "Persian",
                     "country_english": "Iran"},
        "num_symbols": max(v[0] for v in ids.values()) + 1,
        "num_speakers": int(meta["n_speakers"]),
        "inference": {"noise_scale": 0.667, "noise_w_scale": 0.8, "length_scale": 1.0},
        "phoneme_type": "espeak",
        "phoneme_id_map": ids,
        "piper_version": "1.2.0",
    }
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)
    return cfg


# ---------------------------------------------------------------------------
# synthesis
# ---------------------------------------------------------------------------
class Voice:
    def __init__(self, model_dir=None, target_hz=None):
        self.model_dir = model_dir or MODEL_DIR
        self.target_hz = float(target_hz or VOICE_PITCH_HZ)
        onnx = os.path.join(self.model_dir, "model.onnx")
        if not os.path.isfile(onnx):
            raise SystemExit("voice missing - run: python3 tools/tts_engine.py --setup")
        from piper import PiperVoice
        self.voice = PiperVoice.load(onnx, config_path=onnx + ".json")
        self.sample_rate = self.voice.config.sample_rate

    def synth(self, text, path, emotion="neutral", length_scale=None, lock=True):
        """Render one line and keep it inside the unit's single voice."""
        import tempfile
        from piper import SynthesisConfig

        e = EMOTIONS.get(emotion, EMOTIONS["neutral"])
        cfg = SynthesisConfig(
            length_scale=length_scale if length_scale is not None else e["length"],
            noise_scale=e["noise"],
            noise_w_scale=0.8,
            normalize_audio=True,
        )
        raw = path + ".raw.wav"
        with wave.open(raw, "wb") as wf:
            self.voice.synthesize_wav(text, wf, syn_config=cfg)

        if not lock:
            shape_to_voice(raw, path, e, 1.0, None)
            os.remove(raw)
            return path

        # Two passes: shift once, measure what actually came out, shift the
        # residual (<=1.5 st) once more. One pass leaves up to 3 st of drift on
        # short lines, which is exactly what made the unit sound like several people.
        ratio = pitch_ratio_for(raw, target_hz=self.target_hz)
        first = path + ".pass1.wav"
        shape_to_voice(raw, first, e, ratio, self.target_hz)
        residual = pitch_ratio_for(first, target_hz=self.target_hz, limit=1.5)
        if abs(12.0 * math.log2(residual)) > 0.2:
            shape_to_voice(first, path, NEUTRAL_SHAPE, residual, self.target_hz)
            os.remove(first)
        else:
            os.replace(first, path)
        os.remove(raw)
        return path


NEUTRAL_SHAPE = dict(length=1.0, noise=0.0, gain=0.0, tone=None)


def pitch_ratio_for(path, target_hz=VOICE_PITCH_HZ, limit=MAX_PITCH_CORRECTION_SEMITONES):
    """How far this clip has to move to sit on the unit's pitch."""
    from pitch_stats import f0_track
    f = f0_track(path)
    if len(f) < 5:
        return 1.0
    measured = float(__import__("numpy").median(f))
    if measured <= 0:
        return 1.0
    semis = 12.0 * __import__("math").log2(target_hz / measured)
    semis = max(-limit, min(limit, semis))
    return 2.0 ** (semis / 12.0)


def shape_to_voice(src, dst, e, ratio=1.0, target_hz=None, ffmpeg=None):
    """Trim, pitch-lock, gentle tone, one compressor, limiter.

    Deliberately minimal: no formant warping and no soft clipping, both of which make
    speech sound synthetic. Pitch is corrected once, with formants preserved, so the
    timbre stays the same on every line.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    af = [
        "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05",
        "areverse",
        "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08",
        "areverse",
    ]
    if abs(ratio - 1.0) > 0.004:
        af.append("rubberband=pitch=%.5f:formant=preserved:pitchq=quality" % ratio)
    if e.get("tone") == "edge":
        af.append("equalizer=f=2600:t=q:w=1.0:g=1.2")
        af.append("equalizer=f=180:t=q:w=1.0:g=-1.0")
    elif e.get("tone") == "dull":
        af.append("equalizer=f=3800:t=q:w=1.0:g=-2.5")
        af.append("equalizer=f=200:t=q:w=1.0:g=1.0")
    if e.get("gain"):
        af.append("volume=%.1fdB" % e["gain"])
    af += [
        # light, slow compression keeps the loud/quiet acting instead of flattening it
        "acompressor=threshold=-14dB:ratio=1.6:attack=15:release=200",
        # a very short, quiet reverb gives the voice a body (a dry 22 kHz mono synth is
        # what reads as "robotic"); mixed low so intelligibility stays intact
        "aecho=in_gain=0.92:out_gain=0.85:delays=28|52:decays=0.16|0.09",
        "alimiter=limit=0.95",
    ]
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-af", ",".join(af),
           "-ac", "1", "-ar", "22050", "-acodec", "pcm_s16le", dst]
    subprocess.run(cmd, check=True)
    return dst


def clean_text(text):
    """espeak chokes on emoji/latin mixed into Persian; keep it speakable."""
    text = text.replace("\u200c", " ")
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--say")
    ap.add_argument("--emotion", default="neutral", choices=sorted(EMOTIONS))
    ap.add_argument("-o", "--out", default="out.wav")
    ap.add_argument("--list-emotions", action="store_true")
    args = ap.parse_args(argv)

    if args.list_emotions:
        for k, v in EMOTIONS.items():
            print("%-10s length=%.2f pitch=%+d formant=%+d drive=%.2f" % (k, v["length"], v["pitch"], v["formant"], v["drive"]))
        return 0
    if args.setup:
        setup()
        return 0
    if not args.say:
        ap.print_help()
        return 1
    v = Voice()
    v.synth(clean_text(args.say), args.out, emotion=args.emotion)
    with wave.open(args.out) as w:
        print("%s  (%.2fs, %d Hz)" % (args.out, w.getnframes() / w.getframerate(), w.getframerate()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
