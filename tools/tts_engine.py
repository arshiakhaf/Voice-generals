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
import numpy as np
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
# VITS exposes two knobs that add randomness: noise_scale (timbre/pitch wobble per run)
# and noise_w_scale (timing jitter). Both are pinned to 0 so the same text always renders
# to the same prosody. That is what stopped the dozer from sounding like two people.
# Natural VITS variation.  Setting these to 0 made every take identical, but also
# flat and buzzy - it was one of the reasons the old renders sounded robotic.
NOISE_SCALE = 0.667
NOISE_W_SCALE = 0.8
TAKES = 10          # natural takes per clause when picking the register
TAKES_MAX = 18      # extra takes if every one of them drifts
MAX_TAKE_DRIFT_ST = 2.2   # a take that slides register inside itself is not usable
TAKE_TOLERANCE_ST = 2.5   # residual shift only; take bank already picked the closest natural take

# Emotion only shifts speaking pace and a small amount of global tone colour.
EMOTIONS = {
    "neutral":   dict(length=1.00, gain=0.0, tone=None),
    "angry":     dict(length=0.94, gain=0.8, tone="edge"),
    "furious":   dict(length=0.90, gain=1.4, tone="edge"),
    "tired":     dict(length=1.14, gain=-0.8, tone="dull"),
    "smug":      dict(length=1.08, gain=0.0, tone=None),
    "panic":     dict(length=0.86, gain=1.0, tone="edge"),
    "scream":    dict(length=0.82, gain=1.8, tone="edge"),
    "muttering": dict(length=1.20, gain=-1.2, tone="dull"),
    "deadpan":   dict(length=1.05, gain=0.0, tone=None),
}

# One target for the whole unit: median f0 of every clip is pulled to this value.
VOICE_PITCH_HZ = 92.0   # the unit's register: whole-line takes land within ~1 st of it on their own
MAX_PITCH_CORRECTION_SEMITONES = 5.5   # beyond this rubberband smears the consonants


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
    def __init__(self, model_dir=None, target_hz=None, pause_ms=110):
        self.model_dir = model_dir or MODEL_DIR
        self.target_hz = float(target_hz or VOICE_PITCH_HZ)
        self.pause_ms = pause_ms
        onnx = os.path.join(self.model_dir, "model.onnx")
        if not os.path.isfile(onnx):
            raise SystemExit("voice missing - run: python3 tools/tts_engine.py --setup")
        from piper import PiperVoice
        self.voice = PiperVoice.load(onnx, config_path=onnx + ".json")
        self.sample_rate = self.voice.config.sample_rate

    def synth(self, text, path, emotion="neutral", length_scale=None, lock=True):
        """Render one line in ONE voice, sentence by sentence.

        Two separate problems made a line sound like two different people:
          1. piper renders one chunk per espeak clause, and each chunk picks its own
             pitch register (101 Hz vs 136 Hz on the same line);
          2. even inside a single utterance the model sometimes drifts a whole
             octave on the last clause.
        So each sentence is rendered as one utterance, several natural takes are
        rendered, and the take already closest to the unit's register is kept.
        Pitch shifting is only a small residual correction, capped - which is what
        keeps the timbre stable instead of sounding synthetic.  The sentences are
        then joined with a real pause, not with a click.
        """
        import tempfile
        from piper import SynthesisConfig
        e = EMOTIONS.get(emotion, EMOTIONS["neutral"])
        target = float(self.target_hz)
        work = tempfile.mkdtemp(prefix="tts_")
        pieces = []
        pauses = []
        sentences = split_clauses(text) or [text]
        if len(sentences) > 1 and len(sentences) != len(split_sentences(text)):
            pass  # clauses are only used to stop mid-sentence register jumps

        for index, sentence in enumerate(sentences):
            cfg = SynthesisConfig(
                length_scale=length_scale if length_scale is not None else e["length"],
                noise_scale=NOISE_SCALE,
                noise_w_scale=NOISE_W_SCALE,
                normalize_audio=False,
            )
            # Take bank.  A take is only usable when it does not slide register
            # inside itself; among the stable ones we keep the take that already sits
            # closest to the unit's pitch, so the pitch shift stays small or vanishes.
            best, best_err, best_pitch = None, 1e9, 0.0
            fallback, fallback_err = None, 1e9
            limit = TAKES_MAX if lock else 1
            k = 0
            while k < limit:
                k += 1
                raw = os.path.join(work, "s%02d_t%02d.wav" % (index, k - 1))
                self.render_utterance(sentence, raw, cfg)
                if not lock:
                    best = raw
                    break
                info = measure(raw)
                pitch, drift = info["mode"], info["drift"]
                err = abs(12.0 * math.log2(pitch / target)) if pitch > 0 else 1e9
                if drift <= MAX_TAKE_DRIFT_ST:
                    if err < best_err:
                        best, best_err, best_pitch = raw, err, pitch
                    if best_err <= 0.4:      # already in the unit's register: keep it natural
                        break
                elif err < fallback_err:     # nothing stable yet: remember the least bad take
                    fallback, fallback_err = raw, err
                if k >= TAKES and best is not None and best_err <= 1.2:
                    break                    # close enough; more takes would only cost time
            if best is None:
                best, best_err, best_pitch = fallback, fallback_err, (measure_pitch(fallback) if fallback else 0.0)
            if best is None:
                best = os.path.join(work, "s%02d_empty.wav" % index)
                self.render_utterance(sentence, best, cfg)

            if lock and best_pitch and abs(12.0 * math.log2(best_pitch / target)) > 0.4:
                best = self.lock_to_register(best, target, work, "s%02d" % index)
            pieces.append(best)
            pauses.append(self.pause_ms if sentence.rstrip()[-1:] in ".!?؟…" else int(self.pause_ms * 0.65))

        joined = join_with_variable_pauses(pieces, pauses, 22050)
        joined = declick(joined, 22050)
        line = os.path.join(work, "joined.wav")
        audio_to_wav(joined, line)

        # Verify the assembled line, not just its clauses: joining takes can still
        # leave the whole line off the unit's register, and that check is the one
        # that decides whether a line sounds like a different person.
        if lock:
            line = self.lock_to_register(line, target, work, "line", rounds=3)

        # the emotion's tone and level are applied once, to the whole line
        shape_to_voice(line, path, e, 1.0, target)
        shutil.rmtree(work, ignore_errors=True)
        return path


    def lock_to_register(self, path, target, work, tag, tolerance=0.6, rounds=4):
        """Shift a take onto the unit's pitch by feedback, not by one blind guess.

        The model wanders between two registers (about 74 Hz and 133 Hz), so the
        first measurement can under-read: each round re-measures the audio that was
        actually produced and corrects only what is still wrong, by at most 3
        semitones at a time.  Formants are preserved, so the shift changes the
        register but not the person.
        """
        best, best_err = path, abs(12.0 * math.log2(max(measure_pitch(path), 1e-6) / target))
        for r in range(rounds):
            if best_err <= tolerance:
                break
            got = measure_pitch(best)
            if got <= 0:
                break
            semis = 12.0 * math.log2(target / got)
            semis = max(-3.0, min(3.0, semis))
            out = os.path.join(work, "%s_r%d.wav" % (tag, r))
            shift_pitch(best, out, 2.0 ** (semis / 12.0))
            err = abs(12.0 * math.log2(max(measure_pitch(out), 1e-6) / target))
            if err <= best_err:
                best, best_err = out, err
            else:                     # the shift made it worse: stop, keep the better take
                break
        return best

    def render_utterance(self, text, out_path, cfg):
        """One VITS utterance for the whole line: merged phonemes, no seams."""
        groups = self.voice.phonemize(clean_text(text))
        merged = []
        for i, group in enumerate(groups):
            if i and merged and merged[-1] != " ":
                merged.append(" ")
            merged.extend(group)
        if not merged:
            merged = [" "]
        ids = self.voice.phonemes_to_ids(merged)
        audio = np.asarray(self.voice.phoneme_ids_to_audio(ids, cfg), dtype=np.float32).reshape(-1)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1e-8:
            audio = audio / peak * 0.89
        _write(audio, self.voice.config.sample_rate, out_path)
        return out_path


def measure(path):
    """Register + internal drift of a take (dict with mode/drift/n)."""
    try:
        from pitch_stats import register
        return register(path)
    except Exception:
        return dict(mode=0.0, drift=0.0, n=0)


def measure_pitch(path):
    """The take's register (density mode of its f0), used to pick and correct takes."""
    return measure(path)["mode"]


def max_abs_normalise(src, dst, ceiling=0.89):
    a, sr = _read(src)
    peak = float(np.max(np.abs(a))) or 1.0
    _write(a / peak * ceiling, sr, dst)
    return dst


def declick(a, sr, ms=6.0):
    """Ramp the edges of every non-silent island so a join can never click."""
    n = int(sr * ms / 1000.0)
    if n < 2 or len(a) < 3 * n:
        return a
    quiet = np.abs(a) < 1e-4
    # ramp in/out wherever speech starts or stops
    edges = np.flatnonzero(np.diff(quiet.astype(np.int8)))
    out = a.copy()
    for e in edges:
        if quiet[e] and not quiet[e + 1]:          # onset
            seg = slice(e + 1, min(len(out), e + 1 + n))
            out[seg] *= np.linspace(0.0, 1.0, seg.stop - seg.start, dtype=np.float32)
        elif not quiet[e] and quiet[e + 1]:        # offset
            seg = slice(max(0, e + 1 - n), e + 1)
            out[seg] *= np.linspace(1.0, 0.0, seg.stop - seg.start, dtype=np.float32)
    return out


def join_with_variable_pauses(paths_or_arrays, pauses_ms, sr):
    out = []
    for i, item in enumerate(paths_or_arrays):
        if i:
            gap_ms = pauses_ms[i] if i < len(pauses_ms) else pauses_ms[-1]
            out.append(np.zeros(int(sr * gap_ms / 1000.0), dtype=np.float32))
        a, _ = _read(item) if isinstance(item, str) else (item, sr)
        out.append(np.asarray(a, dtype=np.float32).reshape(-1))
    if not out:
        return np.zeros(1, dtype=np.float32)
    return np.concatenate(out)


def join_with_pauses(paths_or_arrays, pause_ms, sr):
    import numpy as np
    gap = np.zeros(int(sr * pause_ms / 1000.0), dtype=np.float32)
    out = []
    for i, item in enumerate(paths_or_arrays):
        if isinstance(item, str):
            item, _ = _read(item)
        if i:
            out.append(gap)
        out.append(np.asarray(item, dtype=np.float32).reshape(-1))
    if not out:
        return np.zeros(1, dtype=np.float32)
    return np.concatenate(out)


def audio_to_wav(a, path, sr=22050):
    _write(a, sr, path)
    return path


def _read(path):
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if ch == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    return a, sr


def _write(a, sr, path):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
    return path


NEUTRAL_SHAPE = dict(length=1.0, gain=0.0, tone=None)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟…])\s+")


CLAUSE_SPLIT = re.compile(r"(?<=[.!?؟…؛:,،])\s+")


def split_clauses(text):
    """Sentences and comma/semicolon clauses: the model changes register inside these."""
    parts = [p.strip() for p in CLAUSE_SPLIT.split(text.strip()) if p.strip()]
    return parts or [text.strip()]


def split_sentences(text):
    parts = [p.strip() for p in SENTENCE_SPLIT.split(text) if p.strip()]
    return parts or [text]


def render_sentences(voice, text, out_path, cfg, pause_ms=110, sr=None):
    """Render each sentence, then join with a fixed pause.

    Deliberately NOT using Piper's synthesize_wav: that concatenates sentence chunks
    sample-to-sample, so a full stop becomes a 3 ms click and every sentence starts a
    fresh prosody contour. A real pause plus one continuous pitch pass fixes both.
    """
    import numpy as np
    pieces = []
    for i, sentence in enumerate(split_sentences(text)):
        audio = voice.phoneme_ids_to_audio(_sentence_ids(voice, sentence), syn_config=cfg)
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if i:
            gap = np.zeros(int((sr or voice.config.sample_rate) * pause_ms / 1000.0), dtype=np.float32)
            pieces.append(gap)
        pieces.append(audio)
    joined = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    peak = float(np.max(np.abs(joined))) or 1.0
    joined = joined / peak * 0.89
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(voice.config.sample_rate)
        wf.writeframes((np.clip(joined, -1, 1) * 32767).astype("<i2").tobytes())
    return out_path


def _sentence_ids(voice, sentence):
    from piper.phoneme_ids import phonemes_to_ids
    groups = voice.phonemize(sentence)
    ids = []
    for group in groups:
        ids.extend(phonemes_to_ids(group, voice.config.phoneme_id_map))
    return ids


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


def shift_pitch(src, dst, ratio, ffmpeg=None):
    """Pitch-only shift with formants preserved (no tempo/EQ/level change)."""
    ffmpeg = ffmpeg or find_ffmpeg()
    af = "rubberband=pitch=%.6f:formant=preserved:pitchq=quality" % ratio
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src, "-af", af,
                    "-ac", "1", "-ar", "22050", "-acodec", "pcm_s16le", dst], check=True)
    return dst


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
        # very light, slow compression: keeps the acting, avoids the "inside a tin" sound
        "acompressor=threshold=-16dB:ratio=1.5:attack=20:release=250",
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
