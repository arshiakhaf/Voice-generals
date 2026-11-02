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

    def test_objects_exist(self):
        objects = json.load(open(os.path.join(ROOT, "data", "object_voices.json")))
        for path in packs():
            pack, _, _ = load_pack(path)
            for unit in pack["units"]:
                self.assertIn(unit["object"], objects)


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
