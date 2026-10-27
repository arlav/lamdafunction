# SPDX-License-Identifier: MIT OR Apache-2.0
"""Turn a Markdown document into a PDF with headless Google Chrome.

Usage, from the repository root::

    python scripts/md2pdf.py docs/pipes-tutorial.md [docs/pipes-tutorial.pdf]

Covers what the docs use: headings, paragraphs, ordered and bulleted lists,
pipe tables, fenced code blocks, inline code, bold, links and <bare URLs>. No Python packages needed; the
HTML is written next to the output and printed with Chrome
(``--headless --print-to-pdf``). Chrome is looked up on PATH, then in the
usual install folders.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def find_chrome() -> str:
    """The Chrome or Chromium binary, or exit with a message."""
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    sys.exit("Google Chrome or Chromium not found; install one or open the .html in a browser and print to PDF")


if len(sys.argv) < 2:
    sys.exit(__doc__)
source = Path(sys.argv[1])
output = Path(sys.argv[2]) if len(sys.argv) > 2 else source.with_suffix(".pdf")

src = source.read_text(encoding="utf-8").splitlines()
out = []

def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"&lt;(https?://[^&\s]+)&gt;", r'<a href="\1">\1</a>', s)
    return s

i = 0
para = []
def flush():
    global para
    if para:
        out.append("<p>" + inline(" ".join(para)) + "</p>")
        para = []

while i < len(src):
    line = src[i]
    if line.startswith("```"):
        flush(); i += 1; block = []
        while i < len(src) and not src[i].startswith("```"):
            block.append(src[i]); i += 1
        i += 1
        out.append("<pre><code>" + html.escape("\n".join(block), quote=False) + "</code></pre>")
    elif m := re.match(r"^(#{1,6})\s+(.*)", line):
        flush(); n = len(m.group(1)); out.append(f"<h{n}>{inline(m.group(2))}</h{n}>"); i += 1
    elif line.startswith("|"):
        flush(); rows = []
        while i < len(src) and src[i].startswith("|"):
            cells = [c.strip() for c in src[i].strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                rows.append(cells)
            i += 1
        out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>")
        for r in rows[1:]:
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
        out.append("</tbody></table>")
    elif re.match(r"^\d+\.\s", line) or line.startswith("- "):
        flush(); tag = "ol" if re.match(r"^\d+\.", line) else "ul"; out.append(f"<{tag}>")
        while i < len(src) and (re.match(r"^\d+\.\s", src[i]) or src[i].startswith("- ")):
            item = re.sub(r"^(\d+\.|-)\s+", "", src[i]); i += 1
            while i < len(src) and src[i].startswith("   ") and src[i].strip():
                item += " " + src[i].strip(); i += 1
            out.append(f"<li>{inline(item)}</li>")
        out.append(f"</{tag}>")
    elif line.strip() == "":
        flush(); i += 1
    else:
        para.append(line.strip()); i += 1
flush()

title = next((re.sub(r"^#\s+", "", l) for l in src if l.startswith("# ")), "Document")
css = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 11pt; line-height: 1.45; color: #1a1a1a; max-width: 100%; }
h1 { font-size: 22pt; margin: 0 0 6pt; border-bottom: 2px solid #1a1a1a; padding-bottom: 4pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; color: #222; }
p { margin: 0 0 8pt; }
li { margin-bottom: 4pt; }
code { font-family: Menlo, Consolas, monospace; font-size: 9.5pt; background: #f1f1f1; padding: 1px 4px; border-radius: 3px; }
pre { background: #f1f1f1; padding: 6pt 8pt; border-radius: 3px; margin: 4pt 0 10pt; }
pre code { background: none; padding: 0; }
a { color: #1a4fa0; }
table { border-collapse: collapse; margin: 6pt 0 10pt; }
th, td { border: 1px solid #bbb; padding: 4pt 8pt; text-align: left; vertical-align: top; }
th { background: #eee; }
strong { font-weight: 600; }
h2, table, li { page-break-inside: avoid; }
h1, h2 { page-break-after: avoid; }
"""
doc = f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{css}</style></head><body>" + "\n".join(out) + "</body></html>"
html_path = output.with_suffix(".html")
html_path.write_text(doc, encoding="utf-8")
subprocess.run(
    [find_chrome(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={output.resolve()}", html_path.resolve().as_uri()],
    check=True,
    capture_output=True,
)
html_path.unlink()
print(f"wrote {output}")
