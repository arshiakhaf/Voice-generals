#!/usr/bin/env python3
"""Fetch the original (vanilla) Zero Hour INI tables this repo builds on top of.

The game's own .ini files are the single source of truth for "which wav file does
this unit slot play", so the pack is generated from them instead of from memory.

Files land in .cache/vanilla (git-ignored, EA's data never gets committed).

Usage:
    python3 tools/fetch_vanilla.py            # Voice.ini, SoundEffects.ini, Object/*.ini
    python3 tools/fetch_vanilla.py --objects  # force refresh the Object/*.ini set
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache", "vanilla")
OBJ_CACHE = os.path.join(CACHE, "Object")

REPO = "TheSuperHackers/GeneralsGamePatch"
BASE = "Patch104pZH/GameFilesOriginalZH/Data/INI"
TOP_FILES = ["Voice.ini", "SoundEffects.ini"]


def _gh_api(path):
    """Fetch a file through the GitHub API (works with `gh auth login`)."""
    if shutil.which("gh"):
        out = subprocess.run(["gh", "api", "repos/%s/contents/%s" % (REPO, path),
                              "--jq", ".content"], capture_output=True)
        if out.returncode == 0 and out.stdout.strip():
            return base64.b64decode(out.stdout.strip())
    url = "https://api.github.com/repos/%s/contents/%s" % (REPO, path)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "voice-generals"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    return base64.b64decode(payload["content"])


def _api_list(path):
    if shutil.which("gh"):
        out = subprocess.run(["gh", "api", "repos/%s/contents/%s" % (REPO, path),
                              "--jq", ".[].name"], capture_output=True, text=True)
        if out.returncode == 0:
            return [n for n in out.stdout.split() if n]
    url = "https://api.github.com/repos/%s/contents/%s" % (REPO, path)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "voice-generals"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return [item["name"] for item in json.load(resp)]


def fetch(rel_path, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    data = _gh_api("%s/%s" % (BASE, rel_path))
    # EA shipped CRLF; normalise so diffs and parses stay stable.
    open(dest, "wb").write(data.replace(b"\r\n", b"\n"))
    print("  %-34s %7d bytes" % (rel_path, len(data)))


def main(argv):
    os.makedirs(CACHE, exist_ok=True)
    print("vanilla INI -> %s" % CACHE)
    for name in TOP_FILES:
        dest = os.path.join(CACHE, name)
        if os.path.isfile(dest) and len(argv) == 1:
            print("  %-34s cached" % name)
            continue
        fetch(name, dest)

    objects_dir = "%s/Object" % BASE
    have = os.listdir(OBJ_CACHE) if os.path.isdir(OBJ_CACHE) else []
    names = [n for n in _api_list(objects_dir) if n.lower().endswith(".ini")]
    todo = [n for n in names if n not in have] if "--objects" not in argv else names
    print("Object/*.ini: %d listed, %d to fetch" % (len(names), len(todo)))
    for name in todo:
        fetch("Object/%s" % name, os.path.join(OBJ_CACHE, name))
    print("done. now run: python3 tools/build_data.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
