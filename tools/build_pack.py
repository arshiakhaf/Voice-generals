#!/usr/bin/env python3
"""Turn a pack definition (data/packs/*.json) into game-ready wav files.

A pack file lists, per unit slot (VoiceSelect, VoiceMove, ...), the vanilla event name
and then one entry per original wav. We keep the exact vanilla file names, so the pack
can be dropped into the game without touching any .ini:

  * loose files  -> <game>/Data/Audio/Sounds/  (what the engine reads first)
  * packed files -> build a .big and mount it with -mod

Usage:
    python3 tools/build_pack.py data/packs/usa_dozer.json --list       # plan only
    python3 tools/build_pack.py data/packs/usa_dozer.json --engine espeak
    python3 tools/build_pack.py data/packs/usa_dozer.json --audio-dir tts_out
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BUILD = os.path.join(ROOT, "build")

sys.path.insert(0, os.path.join(ROOT, "tools"))
from big import BigArchive  # noqa: E402


# ---------------------------------------------------------------------------
# pack model
# ---------------------------------------------------------------------------
def load_pack(path):
    pack = json.load(open(path, encoding="utf-8"))
    events = json.load(open(os.path.join(DATA, "voice_events.json")))
    objects = json.load(open(os.path.join(DATA, "object_voices.json")))
    return pack, events, objects


def plan(pack, events, objects):
    """Expand a pack into a flat list of render jobs, with validation."""
    jobs, problems = [], []
    for unit in pack["units"]:
        obj = objects.get(unit["object"])
        if obj is None:
            problems.append("unknown object %r" % unit["object"])
        for event_name, spec in unit["events"].items():
            ev = events.get(event_name)
            if ev is None:
                problems.append("%s: unknown audio event %r" % (unit["object"], event_name))
                continue
            vanilla = list(ev["sounds"])
            scripted = [f["wav"] for f in spec["files"]]
            # every scripted file must exist in the vanilla event...
            for wav in scripted:
                if wav not in vanilla:
                    problems.append("%s: %r is not part of vanilla event %s"
                                    % (unit["object"], wav, event_name))
            # ...and every vanilla file should be covered, otherwise the pack is partial
            for wav in vanilla:
                if wav not in scripted:
                    problems.append("%s/%s: missing line for %s (%d of %d covered)"
                                    % (unit["object"], event_name, wav, len(scripted), len(vanilla)))
            for f in spec["files"]:
                jobs.append({
                    "object": unit["object"],
                    "event": event_name,
                    "label_fa": spec.get("label_fa", event_name),
                    "wav": f["wav"],
                    "line": f["line_fa"],
                    "tags": f.get("tags", []),
                })
    return jobs, problems


# ---------------------------------------------------------------------------
# audio helpers
# ---------------------------------------------------------------------------
def wav_info(path):
    with wave.open(path, "rb") as w:
        return {"rate": w.getframerate(), "channels": w.getnchannels(),
                "frames": w.getnframes(), "width": w.getsampwidth(),
                "seconds": w.getnframes() / float(w.getframerate())}


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # bundled binary, handy on machines without a system ffmpeg
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def convert_to_game_wav(src, dst, fmt):
    """Downmix/convert any wav to the game's expected mono PCM wav."""
    ffmpeg = find_ffmpeg()
    rate, channels, bits = fmt["sample_rate"], fmt["channels"], fmt["bits"]
    if ffmpeg:
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src,
               "-ac", str(channels), "-ar", str(rate), "-acodec", "pcm_s16le", dst]
        subprocess.run(cmd, check=True)
        return
    # pure-python fallback: only handles PCM wav with matching rate
    with wave.open(src, "rb") as r:
        if r.getframerate() != rate:
            raise RuntimeError("need ffmpeg to resample %s -> %d Hz" % (src, rate))
        frames = r.readframes(r.getnframes())
        if bits != 16 or r.getsampwidth() != 2:
            raise RuntimeError("need ffmpeg to convert %s to 16 bit" % src)
        if channels == 1 and r.getnchannels() == 2:
            import audioop
            frames = audioop.tomono(frames, 2, 0.5, 0.5)
        with wave.open(dst, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)


def synthesize_placeholder(job, path, fmt):
    """A beep of a deterministic pitch per event, so pipelines can be tested.

    Real voices come from the TTS step; this only exists so `--engine tone`
    produces a complete, installable pack without pretending to be speech.
    """
    import math
    rate = fmt["sample_rate"]
    base = 180 + (hash(job["event"]) % 12) * 30
    seconds = 0.35
    frames = bytearray()
    for i in range(int(rate * seconds)):
        env = min(1.0, i / (0.02 * rate)) * min(1.0, (rate * seconds - i) / (0.05 * rate))
        val = int(9000 * env * math.sin(2 * math.pi * base * i / rate))
        frames += struct.pack("<h", max(-32768, min(32767, val)))
    with wave.open(path, "wb") as w:
        w.setnchannels(fmt["channels"])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def synthesize_espeak(job, path, fmt, lang="fa"):
    """Optional local TTS (espeak-ng) so the pipeline can be heard end to end."""
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("espeak-ng not installed")
    tmp = path + ".raw.wav"
    subprocess.run([exe, "-v", lang, "-s", "150", "-p", "45", "-w", tmp, job["line"]], check=True)
    convert_to_game_wav(tmp, path, fmt)
    os.remove(tmp)


# ---------------------------------------------------------------------------
# packaging
# ---------------------------------------------------------------------------
def write_install_layout(pack, jobs, out_dir, fmt):
    """<out>/dozer/Data/Audio/Sounds/<wav>.wav  (drop the folder onto the game root)."""
    sound_dir = os.path.join(out_dir, "Data", "Audio", "Sounds")
    os.makedirs(sound_dir, exist_ok=True)
    manifest = []
    for job in jobs:
        src = job.get("rendered")
        if not src:
            continue
        dst = os.path.join(sound_dir, job["wav"] + ".wav")
        convert_to_game_wav(src, dst, fmt)
        info = wav_info(dst)
        manifest.append({"wav": job["wav"], "event": job["event"], "object": job["object"],
                         "label_fa": job["label_fa"], "line_fa": job["line"],
                         "tags": job["tags"], "seconds": round(info["seconds"], 3)})
    json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w"),
              ensure_ascii=False, indent=1)
    return sound_dir, manifest


def write_big(pack, sound_dir, big_path):
    files = []
    for name in sorted(os.listdir(sound_dir)):
        if name.endswith(".wav"):
            files.append(("Data\\Audio\\Sounds\\%s" % name, os.path.join(sound_dir, name)))
    return BigArchive.write(big_path, files)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pack")
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    ap.add_argument("--engine", choices=["tone", "espeak", "audio-dir"], default="audio-dir")
    ap.add_argument("--audio-dir", help="folder of <wav>.wav renders (--engine audio-dir)")
    ap.add_argument("--out", default=os.path.join(BUILD))
    ap.add_argument("--big", action="store_true", help="also emit a .big archive")
    args = ap.parse_args(argv)

    pack, events, objects = load_pack(args.pack)
    fmt = pack.get("format", {"sample_rate": 22050, "channels": 1, "bits": 16})
    jobs, problems = plan(pack, events, objects)

    print("pack      : %s (%s)" % (pack["pack"], pack["title_fa"]))
    print("units     : %d   events: %d   files: %d"
          % (len(pack["units"]), len({j["event"] for j in jobs}), len(jobs)))
    if problems:
        print("\nPROBLEMS (%d):" % len(problems))
        for p in problems:
            print("  - " + p)
    if args.list:
        for job in jobs:
            print("  %-8s %-26s %s" % (job["wav"], job["event"], job["line"]))
        return 1 if problems else 0

    render_dir = os.path.join(args.out, pack["pack"], "_rendered")
    os.makedirs(render_dir, exist_ok=True)
    for job in jobs:
        dst = os.path.join(render_dir, job["wav"] + ".wav")
        if args.engine == "tone":
            synthesize_placeholder(job, dst, fmt)
        elif args.engine == "espeak":
            synthesize_espeak(job, dst, fmt)
        else:
            if not args.audio_dir:
                ap.error("--engine audio-dir needs --audio-dir")
            src = os.path.join(args.audio_dir, job["wav"] + ".wav")
            if not os.path.isfile(src):
                print("  missing render: %s" % src)
                continue
            shutil.copyfile(src, dst)
        job["rendered"] = dst

    out_dir = os.path.join(args.out, pack["pack"], "mod")
    sound_dir, manifest = write_install_layout(pack, jobs, out_dir, fmt)
    print("\nsounds    : %d wav -> %s" % (len(manifest), sound_dir))
    if args.big:
        big = os.path.join(BUILD, pack["pack"] + ".big")
        total = write_big(pack, sound_dir, big)
        print("big       : %s (%d bytes)" % (big, total))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
