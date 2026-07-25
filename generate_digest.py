#!/usr/bin/env python3
"""
Build a static digest.json for GitHub Pages.
Run by .github/workflows/newsfeed.yml on a schedule and on manual dispatch.
"""

import json
import os

from news_digest import build_digest

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "digest.json")

if __name__ == "__main__":
    digest = build_digest()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(digest, f, indent=2)
    print(f"Wrote {OUT_PATH}")
    if digest["errors"]:
        print("Source errors:", digest["errors"])
