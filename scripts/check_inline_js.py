"""Check every inline <script> in the HTML shell with Node's JavaScript parser."""
from __future__ import annotations
import re
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
page=(ROOT/"index.html").read_text(encoding="utf-8")
scripts=re.findall(r"<script\b[^>]*>([\s\S]*?)</script\s*>",page,flags=re.I)
inline=[content for content in scripts if content.strip()]
if len(inline)<2:
    raise SystemExit("Expected at least two inline scripts in index.html")
with tempfile.TemporaryDirectory(prefix="inline-js-check-") as temp:
    for index,content in enumerate(inline):
        target=Path(temp)/f"script-{index}.js"
        target.write_text(content,encoding="utf-8")
        subprocess.run(["node","--check",str(target)],check=True)
print(f"Inline JS syntax: PASS ({len(inline)} scripts)")
