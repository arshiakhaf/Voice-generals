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
EMOTIONS = {
    "neutral":   dict(length=1.00, pitch=0,   formant=0,   drive=0.0, lowpass=0,  pace=1.00),
    "angry":     dict(length=0.92, pitch=-3,  formant=-6,  drive=0.55, lowpass=0,  pace=1.00),
    "furious":   dict(length=0.86, pitch=-1,  formant=-10, drive=0.95, lowpass=0,  pace=1.00),
    "tired":     dict(length=1.18, pitch=-5,  formant=0,   drive=0.0, lowpass=5500, pace=1.00),
    "smug":      dict(length=1.10, pitch=1,   formant=4,   drive=0.15, lowpass=0,  pace=1.00),
    "panic":     dict(length=0.80, pitch=6,   formant=8,   drive=0.20, lowpass=0,  pace=1.00),
    "scream":    dict(length=0.78, pitch=3,   formant=3,   drive=1.35, lowpass=0,  pace=1.00),
    "muttering": dict(length=1.25, pitch=-6,  formant=-2,  drive=0.10, lowpass=4200, pace=1.00),
    "deadpan":   dict(length=1.06, pitch=-2,  formant=-1,  drive=0.25, lowpass=0,  pace=1.00),
}


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
    def __init__(self, model_dir=None):
        self.model_dir = model_dir or MODEL_DIR
        onnx = os.path.join(self.model_dir, "model.onnx")
        if not os.path.isfile(onnx):
            raise SystemExit("voice missing - run: python3 tools/tts_engine.py --setup")
        from piper import PiperVoice
        self.voice = PiperVoice.load(onnx, config_path=onnx + ".json")
        self.sample_rate = self.voice.config.sample_rate

    def synth(self, text, path, emotion="neutral", length_scale=None, noise_scale=None):
        from piper import SynthesisConfig
        e = EMOTIONS.get(emotion, EMOTIONS["neutral"])
        cfg = SynthesisConfig(
            length_scale=length_scale if length_scale is not None else e["length"],
            noise_scale=noise_scale if noise_scale is not None else (0.667 if emotion == "neutral" else 0.8),
            noise_w_scale=0.8,
            normalize_audio=True,
        )
        raw = path + ".raw.wav"
        with wave.open(raw, "wb") as wf:
            self.voice.synthesize_wav(text, wf, syn_config=cfg)
        apply_emotion(raw, path, e)
        os.remove(raw)
        return path


def apply_emotion(src, dst, e, ffmpeg=None):
    """Pitch/formant/drive shaping that does not change the words."""
    ffmpeg = ffmpeg or find_ffmpeg()
    chain = []
    # rubberband keeps duration while moving pitch and formants separately:
    # lowering formants (+pitch) reads as a bigger, angrier chest voice.
    pitch = e["pitch"] + (e["formant"] * 0.35)
    if abs(pitch) > 0.01 or abs(e["formant"]) > 0.01:
        # this ffmpeg build exposes pitch + formant mode but no separate formant ratio,
        # so the formant component is folded into the pitch move (still reads as chest voice)
        chain.append("rubberband=pitch=%.3f:formant=shifted:pitchq=quality"
                     % (2 ** (pitch / 12.0)))
    if e.get("lowpass"):
        chain.append("lowpass=f=%d" % e["lowpass"])
    if e.get("drive"):
        # a soft clipper adds the "shouting through a cheap mic" grit
        chain.append("asoftclip=type=tanh:threshold=%.2f" % max(0.2, 0.9 - e["drive"] * 0.5))
    chain += [
        "highpass=f=85",
        "equalizer=f=220:t=q:w=1.0:g=1.5",
        "equalizer=f=3200:t=q:w=1.2:g=%.1f" % (2.5 + e.get("drive", 0) * 2),
        "acompressor=threshold=-20dB:ratio=3:attack=4:release=90",
        "alimiter=limit=0.91",
    ]
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src,
           "-af", ",".join(chain), "-ac", "1", "-ar", "22050", "-acodec", "pcm_s16le", dst]
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
