#!/usr/bin/env python3
"""Minimal reader/writer for the SAGE .big archive format used by C&C Generals: ZH.

Layout (verified against the GPL engine source, Core/GameEngine/Source/Common/System/BIGFileSystem.cpp):

    header   char[4]  "BIGF"
             uint32   archive file size            (little endian)
             uint32   number of entries            (big endian)
             uint32   offset of first data entry    (big endian, normally 16)
    entry[]  uint32   file offset  (big endian, 4 byte aligned)
             uint32   file size    (big endian)
             char[]   "Data\\Audio\\Sounds\\file.wav\0"

The engine lowercases every path while loading, so the archives are written with
backslash separators and the exact case the game itself ships.

CLI:
    python3 tools/big.py list  Some.big
    python3 tools/big.py extract Some.big out_dir [name-filter]
    python3 tools/big.py create  out.big file1 file2 ...
"""

import os
import struct
import sys

MAGIC = b"BIGF"
HEADER_SIZE = 16


class BigArchive:
    def __init__(self, path=None):
        self.path = path
        self.entries = []  # list of dicts: name (str, backslashes), offset, size, data (bytes|None)

    # ---------- reading ----------
    @classmethod
    def open(cls, path):
        arc = cls(path)
        with open(path, "rb") as fh:
            blob = fh.read()
        if len(blob) < HEADER_SIZE or blob[:4] != MAGIC:
            raise ValueError("%s is not a BIG archive (bad magic)" % path)
        arc._blob = blob
        arc.archive_size = struct.unpack("<I", blob[4:8])[0]
        count, first = struct.unpack(">II", blob[8:16])
        if first != HEADER_SIZE:
            raise ValueError("%s: unexpected first entry offset %d" % (path, first))
        cursor = HEADER_SIZE
        for _ in range(count):
            offset, size = struct.unpack(">II", blob[cursor:cursor + 8])
            cursor += 8
            name = bytearray()
            while True:
                ch = blob[cursor:cursor + 1]
                cursor += 1
                if not ch:
                    raise ValueError("%s: truncated index table" % path)
                if ch == b"\0":
                    break
                name += ch
            arc.entries.append({"name": name.decode("latin-1"),
                                "offset": offset, "size": size, "data": None})
        return arc

    def read(self, entry):
        if entry["data"] is None:
            entry["data"] = self._blob[entry["offset"]:entry["offset"] + entry["size"]]
        return entry["data"]

    def get(self, name):
        key = name.replace("/", "\\").lower()
        for e in self.entries:
            if e["name"].lower() == key:
                return e
        return None

    # ---------- writing ----------
    @staticmethod
    def write(path, files):
        """files: list of (archivename, bytes) or (archivename, path-on-disk)."""
        norm = []
        seen = set()
        for name, payload in files:
            name = name.replace("/", "\\")
            if name.lower() in seen:
                raise ValueError("duplicate archive path: %s" % name)
            seen.add(name.lower())
            if isinstance(payload, str):
                payload = open(payload, "rb").read()
            norm.append((name, payload))

        def align(n):
            return (n + 3) & ~3

        index_end = HEADER_SIZE + sum(8 + len(n.encode("latin-1")) + 1 for n, _ in norm)
        offset = align(index_end)

        table = bytearray()
        body = bytearray()
        for name, payload in norm:
            raw = name.encode("latin-1")
            table += struct.pack(">II", offset, len(payload)) + raw + b"\0"
            body += payload
            pad = align(len(payload)) - len(payload)
            body += b"\0" * pad
            offset = align(offset + len(payload))

        # The engine (and FinalBIG) record the end of the last payload, unpadded.
        total = (offset - (align(len(norm[-1][1])) - len(norm[-1][1]))) if norm else index_end
        head = MAGIC + struct.pack("<I", total) + struct.pack(">II", len(norm), HEADER_SIZE)
        blob = head + bytes(table)
        blob += b"\0" * (align(index_end) - len(blob))
        blob += bytes(body)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(blob)
        return total


def _main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "list":
        arc = BigArchive.open(argv[1])
        for e in arc.entries:
            print("%9d  %s" % (e["size"], e["name"]))
        print("-- %d entries, %d bytes" % (len(arc.entries), arc.archive_size))
        return 0
    if cmd == "extract":
        arc = BigArchive.open(argv[1])
        out_dir = argv[2]
        needle = argv[3].lower() if len(argv) > 3 else None
        n = 0
        for e in arc.entries:
            if needle and needle not in e["name"].lower():
                continue
            dest = os.path.join(out_dir, e["name"].replace("\\", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").write(arc.read(e))
            n += 1
        print("extracted %d file(s) to %s" % (n, out_dir))
        return 0
    if cmd == "create":
        files = [(os.path.basename(p), p) for p in argv[2:]]
        total = BigArchive.write(argv[1], files)
        print("wrote %s (%d files, %d bytes)" % (argv[1], len(files), total))
        return 0
    print("unknown command: %s" % cmd)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
