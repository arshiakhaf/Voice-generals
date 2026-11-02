#!/usr/bin/env python3
"""Parse the original Generals: Zero Hour INI files into compact JSON databases.

Inputs (cached, never committed - see tools/fetch_vanilla.py):
  .cache/vanilla/Voice.ini            -> AudioEvent definitions (unit voice responses)
  .cache/vanilla/SoundEffects.ini     -> more AudioEvents (structures, drones, vehicles)
  .cache/vanilla/Object/*.ini          -> Object definitions (which event each unit slot uses)

Outputs (committed):
  data/voice_events.json  event name -> {sounds, control, volume, type, priority, ...}
  data/object_voices.json object name -> {file, voice slots, sounds slots}

Why this exists: units in Zero Hour do NOT always use the same voice events. General
specific variants (e.g. AirF_, Lazr_, SupW_, Tank_, Nuke_, Infa_, Boss_) either reuse the
vanilla events or point at their own, so a pack has to be planned per object, not per unit.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache", "vanilla")
DATA = os.path.join(ROOT, "data")

# Slots on an Object that pull an AudioEvent for a unit voice.
VOICE_SLOTS = [
    "VoiceSelect", "VoiceCreate", "VoiceMove", "VoiceGuard", "VoiceAttack",
    "VoiceAttackAir", "VoiceAttackGround", "VoiceTaskComplete", "VoiceNoBuild",
    "VoiceCrush", "VoiceRepair", "VoiceDisarm", "VoiceEnter", "VoiceExit",
    "VoiceDeploy", "VoiceUndeploy", "VoiceBuildResponse", "VoicePrimaryWeapon",
    "VoiceSecondaryWeapon", "VoiceClearMine", "VoiceUnload", "VoiceCombatDrop",
    "VoiceFear", "VoiceDie", "VoiceGarrison", "VoiceCapture", "VoiceSubdue",
    "VoiceClearBuilding", "VoiceSpecialAttack", "VoiceResupply", "VoiceLowFuel",
    "VoiceRally", "VoiceLoopRally", "VoiceLoopSelect", "VoiceSalvage",
    "VoiceGetHealed", "VoiceRetreatToCastle", "VoiceDozerBuildNot",
]
SOUND_SLOTS = [
    "SoundMoveStart", "SoundMoveStartDamaged", "TruckLandingSound",
    "TruckPowerslideSound", "TurretRotateSound", "SoundTurretMoveStart",
]

AUDIO_FIELDS = {
    "volume": str,
    "minvolume": str,
    "limit": str,
    "priority": str,
    "type": str,
    "control": str,
}


def strip_comments(text: str) -> str:
    # INI comments start with ';' to end of line (no inline quoting in these files).
    return "\n".join(line.split(";")[0].rstrip() for line in text.split("\n"))


def parse_audio_events(path: str) -> dict:
    text = strip_comments(open(path, encoding="utf-8", errors="ignore").read().replace("\r", ""))
    events = {}
    for m in re.finditer(r"^AudioEvent\s+(\S+)\s*$(.*?)^End\b", text, re.S | re.M | re.I):
        name, body = m.group(1), m.group(2)
        ev = {"sounds": [], "attack": [], "decay": []}
        for line in body.split("\n"):
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip().lower(), " ".join(val.split())
            if key == "sounds":
                ev["sounds"] = val.split()
            elif key == "attack":
                ev["attack"] = val.split()
            elif key == "decay":
                ev["decay"] = val.split()
            elif key == "pitchshift":
                ev[key] = val
            elif key == "delay":
                ev[key] = val
            elif key in AUDIO_FIELDS:
                ev[key] = val
        events[name] = ev
    return events


def parse_objects(objects_dir: str) -> dict:
    objects = {}
    for fname in sorted(os.listdir(objects_dir)):
        if not fname.lower().endswith(".ini"):
            continue
        path = os.path.join(objects_dir, fname)
        text = strip_comments(open(path, encoding="utf-8", errors="ignore").read().replace("\r", ""))
        for m in re.finditer(r"^Object\s+(\S+)\b(.*?)^End\b", text, re.S | re.M | re.I):
            name, body = m.group(1), m.group(2)
            entry = objects.setdefault(name, {"file": fname, "voice": {}, "sound": {}})
            for line in body.split("\n"):
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if key in VOICE_SLOTS:
                    entry["voice"][key] = val
                elif key in SOUND_SLOTS:
                    entry["sound"][key] = val
    return objects


def main() -> int:
    ini_files = [os.path.join(CACHE, n) for n in ("Voice.ini", "SoundEffects.ini")]
    objects_dir = os.path.join(CACHE, "Object")
    present = [p for p in ini_files if os.path.isfile(p)]
    if not present:
        sys.exit("missing .cache/vanilla/Voice.ini - run tools/fetch_vanilla.py")

    events = {}
    for path in present:
        for name, ev in parse_audio_events(path).items():
            # Voice.ini wins over SoundEffects.ini for duplicated names.
            events.setdefault(name, ev) if path.endswith("SoundEffects.ini") else events.__setitem__(name, ev)
    objects = parse_objects(objects_dir) if os.path.isdir(objects_dir) else {}

    # Drop objects that do not talk at all (rocks, wrecks, projectiles...).
    objects = {k: v for k, v in objects.items() if v["voice"] or v["sound"]}

    missing = sorted({e for o in objects.values() for e in o["voice"].values()
                      if e not in events and not e.startswith("NoSound")})
    if os.path.isdir(DATA) is False:
        os.makedirs(DATA)

    json.dump(events, open(os.path.join(DATA, "voice_events.json"), "w"), indent=1, sort_keys=True)
    json.dump(objects, open(os.path.join(DATA, "object_voices.json"), "w"), indent=1, sort_keys=True)

    print("voice events : %d" % len(events))
    print("objects      : %d (talking only)" % len(objects))
    if missing:
        print("unresolved event names: %d -> %s" % (len(missing), ", ".join(missing[:10])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
