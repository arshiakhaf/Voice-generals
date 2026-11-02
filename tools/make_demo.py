#!/usr/bin/env python3
"""Stitch the rendered lines of a pack into one demo mp3, in script order.

Usage:
    python3 tools/make_demo.py usa_dozer            # -> preview/usa_dozer_demo.mp3
    python3 tools/make_demo.py usa_dozer --gap 0.6
"""

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from build_pack import find_ffmpeg  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pack")
    ap.add_argument("--gap", type=float, default=0.45, help="seconds of silence between lines")
    ap.add_argument("--out", help="output mp3 path")
    args = ap.parse_args(argv)

    pack = json.load(open(os.path.join(ROOT, "data", "packs", args.pack + ".json"), encoding="utf-8"))
    sounds = os.path.join(ROOT, "build", args.pack, "mod", "Data", "Audio", "Sounds")
    names = [f["wav"] for u in pack["units"] for s in u["events"].values() for f in s["files"]]
    names = [n for n in names if os.path.isfile(os.path.join(sounds, n + ".wav"))]
    if not names:
        sys.exit("nothing rendered yet - run tools/build_pack.py first")

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        sys.exit("ffmpeg not found")
    inputs = []
    for n in names:
        inputs += ["-i", os.path.join(sounds, n + ".wav")]
    chains = ["[%d:a]apad=pad_dur=%s[a%d]" % (i, args.gap, i) for i in range(len(names))]
    chains.append("%sconcat=n=%d:v=0:a=1[out]"
                  % ("".join("[a%d]" % i for i in range(len(names))), len(names)))
    out = args.out or os.path.join(ROOT, "preview", args.pack + "_demo.mp3")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run([ffmpeg, "-y", "-loglevel", "error"] + inputs +
                   ["-filter_complex", ";".join(chains), "-map", "[out]",
                    "-ac", "2", "-b:a", "128k", out], check=True)
    print("%d lines -> %s (%d bytes)" % (len(names), out, os.path.getsize(out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
