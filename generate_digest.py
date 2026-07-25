#!/usr/bin/env python3
"""
Build a static digest.json for GitHub Pages.
Run by .github/workflows/newsfeed.yml on a schedule and on manual dispatch.

The custom topic (if any) persists across runs: whatever topic was last
entered via the workflow's manual "topic" input keeps getting re-analyzed
on every scheduled run, until someone enters a new topic or "clear".
"""

import json
import os

from news_digest import build_digest

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "digest.json")
CLEAR_VALUES = {"clear", "none", "remove"}


def load_previous_topic():
    if not os.path.isfile(OUT_PATH):
        return None
    try:
        with open(OUT_PATH) as f:
            prev = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    custom_topic = prev.get("custom_topic")
    return custom_topic["topic"] if custom_topic else None


if __name__ == "__main__":
    topic_input = os.environ.get("TOPIC", "").strip()

    if topic_input.lower() in CLEAR_VALUES:
        topic = None
    elif topic_input:
        topic = topic_input
    else:
        topic = load_previous_topic()

    digest = build_digest(custom_topic=topic)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(digest, f, indent=2)
    print(f"Wrote {OUT_PATH}")
    if digest["errors"]:
        print("Source errors:", digest["errors"])
