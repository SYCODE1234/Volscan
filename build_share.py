"""Bake the latest scan into self-contained HTML that needs no server.

    .venv/bin/python build_share.py

Writes two files into dist/:

  volscan.html           full standalone page — double-click it, or drop it on
                         GitHub Pages / any static host
  volscan-artifact.html  the same page as a fragment, for publishing as a
                         Claude Artifact (which supplies its own <head>)

Both embed data/scan.json, so they are a snapshot: re-run scan.py then this
script to refresh them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "static" / "share_template.html"
SCAN = ROOT / "data" / "scan.json"
DIST = ROOT / "dist"

STANDALONE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{desc}">
{head}
</head>
<body>
{body}
</body>
</html>
"""


def section(text: str, name: str) -> str:
    start, end = f"<!--{name}-->", f"<!--/{name}-->"
    return text.split(start, 1)[1].split(end, 1)[0].strip()


def main() -> int:
    if not SCAN.exists():
        print("No data/scan.json yet — run scan.py first.", file=sys.stderr)
        return 1

    scan = json.loads(SCAN.read_text())
    # Compact, and keep the payload safe to sit inside a <script> block.
    payload = (
        json.dumps(scan, separators=(",", ":"))
        .replace("</", "<\\/")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )

    template = TEMPLATE.read_text()
    head = section(template, "HEAD")
    body = section(template, "BODY").replace("__SCAN_DATA__", payload)

    through = scan["rows"][0]["last_date"] if scan.get("rows") else "?"
    desc = (
        f"S&P 500 volatility screener — {scan['scanned']} constituents ranked by "
        f"realized and 30-day implied volatility, prices through {through}."
    )

    DIST.mkdir(exist_ok=True)
    (DIST / "volscan.html").write_text(
        STANDALONE.format(head=head, body=body, desc=desc)
    )
    (DIST / "volscan-artifact.html").write_text(head + "\n" + body + "\n")

    for f in ("volscan.html", "volscan-artifact.html"):
        size = (DIST / f).stat().st_size
        print(f"dist/{f}  {size / 1e6:.2f} MB")
    print(f"{scan['scanned']} rows · {scan.get('iv_reliable_count', 0)} reliable IV · through {through}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
