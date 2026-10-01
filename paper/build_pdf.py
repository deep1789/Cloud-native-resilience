"""Render paper.md to paper.pdf (markdown -> HTML -> headless Chromium print)."""
import os
import re
import subprocess
import sys

import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
md = open(os.path.join(HERE, "paper.md"), encoding="utf-8").read()
body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
body = body.replace('src="../figures/', 'src="file://' + os.path.abspath(os.path.join(HERE, "..", "figures")) + "/")
css = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: 'Times New Roman', Georgia, serif; font-size: 10.5pt; line-height: 1.35; color: #111; }
h1 { font-size: 17pt; text-align: center; margin: 0 0 6pt; }
h2 { font-size: 12.5pt; margin: 14pt 0 4pt; border-bottom: 0.5pt solid #999; }
h3 { font-size: 11pt; margin: 10pt 0 3pt; }
table { border-collapse: collapse; margin: 6pt auto; font-size: 8.3pt; }
th, td { border: 0.4pt solid #888; padding: 1.5pt 4pt; text-align: left; }
th { background: #eee; }
img { display: block; margin: 6pt auto; max-width: 100%; }
code { font-size: 9pt; background: #f3f3f3; padding: 0 2pt; }
p { text-align: justify; margin: 3pt 0 5pt; }
em.figcap, .figcap { font-size: 9pt; color: #333; display: block; text-align: center; }
"""
html = f"<!doctype html><html><head><meta charset='utf-8'><title>ARC</title><style>{css}</style></head><body>{body}</body></html>"
hp = os.path.join(HERE, "paper.html")
open(hp, "w", encoding="utf-8").write(html)
chrome = None
for root, _, files in os.walk("/opt/pw-browsers"):
    for f in files:
        if f in ("chrome", "headless_shell") and "chromium" in root:
            chrome = os.path.join(root, f)
            break
    if chrome:
        break
if not chrome:
    sys.exit("chromium not found")
out = os.path.join(HERE, "paper.pdf")
subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files",
                f"--print-to-pdf={out}", "--no-pdf-header-footer", "file://" + hp], check=True, capture_output=True)
os.remove(hp)
print("wrote", out, os.path.getsize(out) // 1024, "KiB")
