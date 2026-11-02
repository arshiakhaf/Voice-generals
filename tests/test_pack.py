#!/usr/bin/env python3
"""Checks that a pack covers every vanilla wav file of every event it touches.

Run:  python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from big import BigArchive  # noqa: E402
from build_pack import load_pack, plan  # noqa: E402


def packs():
    d = os.path.join(ROOT, "data", "packs")
    return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".json")]


class PackCoverage(unittest.TestCase):
    def test_every_pack_is_complete(self):
        for path in packs():
            with self.subTest(pack=os.path.basename(path)):
                pack, events, objects = load_pack(path)
                jobs, problems = plan(pack, events, objects)
                # an empty "missing line" report means the pack replaces every file
                missing = [p for p in problems if "missing line" in p]
                self.assertEqual(missing, [])
                self.assertEqual([p for p in problems if "unknown" in p or "not part of" in p], [])
                counts = {}
                for job in jobs:
                    counts[job["event"]] = counts.get(job["event"], 0) + 1
                for event, n in counts.items():
                    files = events[event]["sounds"]
                    self.assertEqual(len(set(files)), len(files))
                    self.assertEqual(n, len(files), "%s: %d/%d wav" % (event, n, len(files)))

    def test_lines_are_not_empty_and_unique(self):
        for path in packs():
            pack, _, _ = load_pack(path)
            seen = set()
            for unit in pack["units"]:
                for event, spec in unit["events"].items():
                    for f in spec["files"]:
                        self.assertTrue(f["line_fa"].strip())
                        self.assertNotIn(f["line_fa"], seen, "duplicate line: %s" % f["wav"])
                        seen.add(f["line_fa"])

    def test_tone_rules(self):
        """The pack declares its own vocabulary rules; enforce them so a later
        rewrite cannot quietly reintroduce banned family insults."""
        for path in packs():
            pack, _, _ = load_pack(path)
            tone = pack.get("tone", {})
            lines = [f["line_fa"] for u in pack["units"]
                     for spec in u["events"].values() for f in spec["files"]]
            text = " ".join(lines)
            for bad in tone.get("banned_dirty", []):
                self.assertNotIn(bad, text, "banned word %r leaked back in" % bad)
            self.assertGreaterEqual(sum(text.count(w) for w in tone.get("allowed_dirty", [])), 40,
                                    "pack is supposed to be explicit")

    def test_every_line_has_emotion_and_speech_text(self):
        for path in packs():
            pack, _, _ = load_pack(path)
            for unit in pack["units"]:
                for event, spec in unit["events"].items():
                    for f in spec["files"]:
                        self.assertIn("emotion", f, "%s: no emotion" % f["wav"])
                        self.assertTrue(f.get("tts"), "%s: no tts text" % f["wav"])
                        self.assertNotEqual(f["emotion"], "neutral",
                                            "%s: every unit line should be acted" % f["wav"])

    def test_objects_exist(self):
        objects = json.load(open(os.path.join(ROOT, "data", "object_voices.json")))
        for path in packs():
            pack, _, _ = load_pack(path)
            for unit in pack["units"]:
                self.assertIn(unit["object"], objects)


class VoiceConsistency(unittest.TestCase):
    """A unit must sound like one person: pitch per clip may act, not wander."""

    def test_rendered_pitch_is_locked(self):
        import glob
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from pitch_stats import pitch_mode
        base = os.path.join(ROOT, "build")
        for pack_path in packs():
            pack, _, _ = load_pack(pack_path)
            sounds = os.path.join(base, pack["pack"], "mod", "Data", "Audio", "Sounds")
            if not os.path.isdir(sounds):
                self.skipTest("pack not rendered yet")
            target = pack.get("voice", {}).get("pitch_hz", 100.0)
            # The register of a line is the density mode of its f0, not the median:
            # a median drifts with the falling tail of a shouted line and let a
            # genuinely wrong take pass an earlier version of this test.
            meds = []
            for wav in glob.glob(os.path.join(sounds, "*.wav")):
                m = pitch_mode(wav)
                if m > 0:
                    meds.append(m)
            self.assertGreater(len(meds), 30)
            median = sorted(meds)[len(meds) // 2]
            # the middle of the pack sits on the declared pitch...
            self.assertLess(abs(12 * __import__("math").log2(median / target)), 1.5,
                            "median %0.1f Hz is not the declared %0.1f Hz" % (median, target))
            # ...and no clip is more than a fifth away from it
            worst = max(abs(12 * __import__("math").log2(m / target)) for m in meds)
            self.assertLess(worst, 4.0, "a clip drifted %.1f semitones from the unit voice" % worst)
            # and the pack is tight, not merely inside the clamp
            q = sorted(12 * __import__("math").log2(m / target) for m in meds)
            iqr = q[int(len(q) * 0.75)] - q[int(len(q) * 0.25)]
            self.assertLess(iqr, 1.5, "pitch spread inside the pack is %.1f semitones" % iqr)


class BigFormat(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        files = [("Data\\Audio\\Sounds\\a.wav", b"RIFF" + bytes(range(64))),
                 ("Data\\Audio\\Sounds\\b.wav", b"RIFF"),
                 ("Data\\INI\\Voice.ini", b"AudioEvent X\nEnd\n")]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.big")
            size = BigArchive.write(out, files)
            # the header records the end of the last payload (no trailing padding)
            self.assertLessEqual(os.path.getsize(out) - size, 3)
            arc = BigArchive.open(out)
            self.assertEqual(len(arc.entries), 3)
            for name, payload in files:
                self.assertEqual(arc.read(arc.get(name)), payload)

    def test_bad_magic(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "bad.big")
            open(out, "wb").write(b"NOPE" + b"\0" * 20)
            with self.assertRaises(ValueError):
                BigArchive.open(out)


if __name__ == "__main__":
    unittest.main()
