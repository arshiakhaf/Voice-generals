#!/usr/bin/env python3
"""Build a single self-contained HTML player for a rendered pack.

The audio is embedded as base64 data URIs, so the page works offline, inside a
sandboxed preview iframe, and even when opened straight from disk - no server or
relative-path juggling needed.

Usage:
    python3 tools/make_player.py usa_dozer            # -> preview/<pack>_player.html
    python3 tools/make_player.py usa_dozer --no-embed  # link audio/*.wav instead
"""

import argparse
import base64
import html
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EMO = {"neutral": "معمولی", "angry": "عصبانی", "furious": "جوشی", "tired": "خسته",
       "smug": "پزدار", "panic": "هراسان", "scream": "دادِ بلند",
       "muttering": "زیرلبی", "deadpan": "خشک"}
EV_FA = {"DozerUSAVoiceSelect": "انتخاب شدن", "DozerUSAVoiceCreate": "از کارخانه بیرون آمدن",
         "DozerUSAVoiceMove": "حرکت کردن", "DozerUSAVoiceCrush": "له کردن پیاده‌نظام",
         "DozerUSAVoiceBuild": "شروع ساخت", "DozerUSAVoiceBuildNot": "نمی‌تونه بسازه",
         "DozerUSAVoiceRepair": "تعمیر کردن", "DozerUSAVoiceBuildComplete": "ساخت تمام شد",
         "DozerUSAVoiceClearMine": "مین‌روبی"}

HEAD = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:Tahoma,system-ui,sans-serif;background:#0f110c;color:#eae8de;padding:20px;max-width:900px;margin:auto}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:#8f927e;font-size:13px;margin-bottom:18px;line-height:1.8}}
h2{{font-size:14px;color:#b7f07a;font-family:monospace;margin:24px 0 8px;border-top:1px solid #262a1e;padding-top:12px}}
h2 .c{{background:#262a1e;color:#b7f07a;border-radius:9px;padding:1px 7px;font-size:11px}}
h2 .l{{color:#8f927e;font-family:Tahoma;font-weight:400}}
.row{{display:flex;gap:12px;align-items:center;padding:9px 4px;border-bottom:1px dashed #20241a}}
.row.on{{background:#171c12;border-radius:8px}}
.row.on .play{{background:#b7f07a;color:#0f110c}}
.txt{{flex:1}}
.meta{{font-size:11px;color:#767a68;font-family:monospace}}
code{{color:#d6d4c6}}.line{{font-size:15px;line-height:1.7;margin-top:2px}}
i{{font-style:normal;font-size:11px;border-radius:8px;padding:1px 6px;margin-inline-start:4px}}
i.e{{background:#3a2a1c;color:#e2b25a}}i.s{{background:#1e2318;color:#8fa87b}}
.play{{width:42px;height:42px;border-radius:50%;border:1px solid #34392a;background:#1a1e15;color:#b7f07a;
font-size:15px;cursor:pointer;flex:0 0 auto}}
.play:hover{{background:#232a1a}}
</style></head><body>
<h1>{title}</h1>
<div class="sub">{subtitle}</div>
"""

TAIL = """
<script>
let cur = null;
function playRow(btn){
  const row = btn.closest('.row');
  if (cur && cur.datasetRow === row) {
    if (cur.paused) cur.play(); else cur.pause();
    return;
  }
  if (cur) cur.pause();
  document.querySelectorAll('.row.on').forEach(function(r){ r.classList.remove('on'); });
  cur = new Audio(row.dataset.src);
  cur.datasetRow = row;
  cur.onended = function(){ row.classList.remove('on'); };
  row.classList.add('on');
  cur.play();
}
</script></body></html>"""


def build(pack, embed=True, mp3=False):
    base = os.path.join(ROOT, "build", pack)
    manifest = json.load(open(os.path.join(base, "mod", "manifest.json"), encoding="utf-8"))
    sounded = os.path.join(base, "mod", "Data", "Audio", "Sounds")
    out_dir = os.path.join(ROOT, "preview")
    os.makedirs(out_dir, exist_ok=True)
    if not embed:
        os.makedirs(os.path.join(out_dir, "audio"), exist_ok=True)

    order, groups = [], {}
    for row in manifest:
        if row["event"] not in groups:
            groups[row["event"]] = []
            order.append(row["event"])
        groups[row["event"]].append(row)

    rows, n = [], 0
    for event in order:
        rows.append('<h2>%s <span class="c">%d</span> <span class="l">%s</span></h2>'
                    % (html.escape(event), len(groups[event]), html.escape(EV_FA.get(event, ""))))
        for row in groups[event]:
            n += 1
            wav = os.path.join(sounded, row["wav"] + ".wav")
            if not os.path.isfile(wav):
                continue
            if embed:
                src = "data:audio/wav;base64," + base64.b64encode(open(wav, "rb").read()).decode()
            elif mp3:
                src = "wav/%s.mp3" % row["wav"]
            else:
                shutil.copyfile(wav, os.path.join(out_dir, "audio", row["wav"] + ".wav"))
                src = "audio/%s.wav" % row["wav"]
            rows.append(
                '<div class="row" data-src="%s"><div class="txt"><div class="meta"><b>%d</b> '
                '<code>%s</code> <i class="e">%s</i> <i class="s">%.1fs</i></div>'
                '<div class="line">%s</div></div>'
                '<button class="play" onclick="playRow(this)">&#9654;</button></div>'
                % (src, n, html.escape(row["wav"]), EMO.get(row.get("emotion"), row.get("emotion", "")),
                   row.get("seconds", 0), html.escape(row["line_fa"])))

    title = "بولدوزر آمریکا — پک «بیل و بودجه»"
    subtitle = ("%d خط، ۹ رویداد صوتی، چند حس مختلف — روی هر دکمه بزن. "
                "صداها داخل همین صفحه‌اند (بدون سرور)." % n)
    page = HEAD.format(title=title, subtitle=subtitle) + "\n".join(rows) + TAIL
    out = os.path.join(out_dir, pack + "_player.html")
    open(out, "w", encoding="utf-8").write(page)
    return out, n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pack")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--mp3", action="store_true", help="link preview/wav/<name>.mp3 instead of embedding")
    args = ap.parse_args(argv)
    out, n = build(args.pack, embed=not (args.no_embed or args.mp3), mp3=args.mp3)
    print("%s (%d lines, %.1f MB)" % (out, n, os.path.getsize(out) / 1048576))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
