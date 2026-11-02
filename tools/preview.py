#!/usr/bin/env python3
"""Build a self-contained HTML page so the whole pack can be auditioned in order.

Reads data/packs/<pack>.json (the full script) plus the rendered wavs in
build/<pack>/mod/Data/Audio/Sounds, and writes preview/<pack>.html so every line
of the script is visible while the already-rendered ones can be played.

Usage:
    python3 tools/preview.py usa_dozer
"""

import html
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
PREVIEW = os.path.join(ROOT, "preview")

TAG_FA = {
    "dirty": "رکیک",
    "dirty-name": "فحش ناموسی",
    "finglish": "فینگلیش",
    "political": "سیاسی",
    "general-mock": "دست‌انداختن ژنرال",
    "work": "مرتبط با کار یونیت",
    "dark": "سیاه",
    "slang": "خوابیده‌بازی",
    "joke": "جوک",
}


def main(argv):
    if not argv:
        sys.exit("usage: preview.py <pack>")
    pack = argv[0]
    pack_path = os.path.join(ROOT, "data", "packs", pack + ".json")
    if not os.path.isfile(pack_path):
        sys.exit("missing %s" % pack_path)
    data = json.load(open(pack_path, encoding="utf-8"))
    sounds = os.path.join(BUILD, pack, "mod", "Data", "Audio", "Sounds")

    # group by event, keeping the pack's declared order
    order, groups = [], {}
    for unit in data["units"]:
        for event, spec in unit["events"].items():
            order.append(event)
            groups[event] = [dict(f, event=event, label_fa=spec.get("label_fa", event))
                             for f in spec["files"]]

    rows = []
    n = 0
    rendered = 0
    for event in order:
        items = groups[event]
        rows.append('<section><h2>%s <span class="count">%d</span> '
                    '<span class="label">%s</span></h2>'
                    % (html.escape(event), len(items),
                       html.escape(items[0].get("label_fa", ""))))
        for row in items:
            n += 1
            src = "audio/%s.wav" % row["wav"]
            ready = os.path.isfile(os.path.join(sounds, row["wav"] + ".wav"))
            rendered += 1 if ready else 0
            tags = " ".join('<i class="t">%s</i>' % TAG_FA.get(t, t) for t in row.get("tags", []))
            player = ('<audio controls preload="none" src="%s"></audio>' % src if ready
                      else '<span class="todo">صدا هنوز ساخته نشده</span>')
            rows.append(
                '<div class="row"><div class="meta"><b>%d.</b> <code>%s</code> %s</div>'
                '<div class="line">%s</div>%s</div>'
                % (n, html.escape(row["wav"]), tags, html.escape(row["line_fa"]), player))
        rows.append("</section>")

    page = """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<title>پک صدا - %(pack)s</title>
<style>
:root{color-scheme:dark}
body{font-family:Tahoma,Vazirmatn,system-ui;background:#12140f;color:#e8e6dc;margin:0;padding:24px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#9a9a86;margin-bottom:24px;font-size:13px}
section{border-top:1px solid #2a2d22;padding:14px 0}
h2{font-size:15px;margin:0 0 10px;color:#b7f07a;font-family:monospace}
h2 .label{color:#8b8b78;font-weight:400;font-family:Tahoma}
h2 .count{background:#2a2d22;padding:1px 7px;border-radius:9px;color:#b7f07a;font-size:12px}
.row{display:grid;grid-template-columns:1fr auto;gap:4px 14px;padding:9px 0;border-bottom:1px dashed #23261c}
.meta{font-size:12px;color:#7e8270;font-family:monospace}
.line{font-size:15px;line-height:1.7}
audio{grid-row:1/3;align-self:center;height:32px}
.todo{grid-row:1/3;align-self:center;font-size:12px;color:#6d7060;border:1px dashed #3a3d2e;border-radius:12px;padding:4px 10px}
code{color:#d8d6c8}
i.t{font-style:normal;font-size:11px;background:#22261a;color:#a8c98a;padding:1px 6px;border-radius:8px;margin-inline-start:5px}
</style></head><body>
<h1>%(title)s</h1>
<div class="sub">%(pack)s &middot; %(n)d خط &middot; %(events)d رویداد &middot; %(rendered)d تا صدا خورده &middot; فرمت بازی: مونو 22050Hz</div>
%(body)s
</body></html>""" % {"pack": html.escape(pack), "n": n, "events": len(order), "rendered": rendered,
                     "title": html.escape(argv[1] if len(argv) > 1 else pack),
                     "body": "\n".join(rows)}

    os.makedirs(PREVIEW, exist_ok=True)
    out = os.path.join(PREVIEW, pack + ".html")
    open(out, "w", encoding="utf-8").write(page)

    # audio lives next to the html so the page is portable
    audio_dir = os.path.join(PREVIEW, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    src_dir = sounds
    copied = 0
    for name in os.listdir(src_dir) if os.path.isdir(src_dir) else []:
        if name.lower().endswith(".wav"):
            target = os.path.join(audio_dir, name)
            with open(os.path.join(src_dir, name), "rb") as s, open(target, "wb") as d:
                d.write(s.read())
            copied += 1
    print("wrote %s (%d lines, %d/%d rendered)" % (out, n, rendered, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
