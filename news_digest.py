#!/usr/bin/env python3
"""
Shared news-aggregation logic: fetches RSS from 3 popular sources per
category, clusters headlines by shared topic, and ranks by cross-source
agreement. Used by both newsfeed_server.py (local dev server) and
generate_digest.py (static build for GitHub Pages).

No API keys required — pulls public RSS feeds only.
"""

import re
import html
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

# Exactly 3 of the most popular sources per category. A story is only
# considered "trending" if it shows up (by shared keywords) across more
# than one of these three.
FEEDS = {
    "ai": [
        ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
        ("Ars Technica", "https://arstechnica.com/ai/feed/"),
    ],
    "world": [
        ("BBC", "http://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("CNN", "http://rss.cnn.com/rss/cnn_world.rss"),
    ],
    "tech": [
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("Ars Technica", "https://arstechnica.com/feed/"),
    ],
    "sports": [
        ("BBC Sport", "http://feeds.bbci.co.uk/sport/rss.xml"),
        ("ESPN", "https://www.espn.com/espn/rss/news"),
        ("CBS Sports", "https://www.cbssports.com/rss/headlines/"),
    ],
    "india": [
        ("Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
        ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
        ("NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ],
}

TOP_N = 3

# Companies used to filter the general tech feeds down to "big tech" news.
BIG_TECH_KEYWORDS = [
    "apple", "google", "alphabet", "amazon", "meta", "facebook", "spacex",
    "tesla", "microsoft", "nvidia", "openai",
]

TAG_RE = re.compile(r"<[^>]+>")
CAP_STOPWORDS = {
    "the", "a", "an", "in", "on", "at", "for", "to", "and", "is", "as", "by",
    "with", "after", "over", "amid", "new", "how", "why", "what", "this",
    "that", "it", "its", "his", "her", "they", "we", "you", "i", "will",
    "can", "says", "say", "not", "are", "be", "was", "were", "has", "have",
    "had", "but", "or", "from", "into", "out", "up", "down", "of", "than",
}


def strip_html(text):
    if not text:
        return ""
    return html.unescape(TAG_RE.sub("", text)).strip()


def extract_entities(title):
    """Pull proper-noun-ish tokens (names, products, model names, places)
    out of a headline. Used as a lightweight cross-source topic signature
    so the same story can be recognized across differently-worded headlines."""
    words = re.findall(r"[A-Z][A-Za-z0-9\-']*", title)
    return {w.lower() for w in words if w.lower() not in CAP_STOPWORDS and len(w) >= 3}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def parse_date(raw):
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def time_ago(dt):
    if dt is None:
        return ""
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 3600:
        return f"{max(seconds // 60, 1)}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


class RedirectHandler308(urllib.request.HTTPRedirectHandler):
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, code, msg, headers)


_opener = urllib.request.build_opener(RedirectHandler308)


def _download(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; LocalNewsfeed/1.0)",
            "Accept-Encoding": "identity",
        },
    )
    with _opener.open(req, timeout=10) as resp:
        return resp.read()


def _parse_items(data):
    """Parse RSS/Atom bytes into raw item dicts. Includes the per-item
    <source> publisher (present on Google News RSS) as _feed_source so
    callers can attribute items to the outlet that actually wrote them."""
    root = ElementTree.fromstring(data)
    nodes = root.findall(".//item")
    is_atom = False
    if not nodes:
        nodes = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        is_atom = True

    items = []
    for node in nodes:
        if is_atom:
            title_el = node.find("{http://www.w3.org/2005/Atom}title")
            link_el = node.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.get("href") if link_el is not None else None
            date_el = node.find("{http://www.w3.org/2005/Atom}updated")
            summary_el = node.find("{http://www.w3.org/2005/Atom}summary")
            source_el = None
        else:
            title_el = node.find("title")
            link_el = node.find("link")
            link = link_el.text.strip() if link_el is not None and link_el.text else None
            date_el = node.find("pubDate")
            summary_el = node.find("description")
            source_el = node.find("source")

        title = strip_html(title_el.text) if title_el is not None and title_el.text else None
        if not title or not link:
            continue

        pub_dt = parse_date(date_el.text) if date_el is not None and date_el.text else None
        summary = strip_html(summary_el.text)[:220] if summary_el is not None and summary_el.text else ""
        feed_source = strip_html(source_el.text) if source_el is not None and source_el.text else None

        items.append({
            "title": title,
            "link": link.strip(),
            "published": pub_dt.isoformat() if pub_dt else None,
            "time_ago": time_ago(pub_dt),
            "_sort_dt": pub_dt or datetime(1970, 1, 1, tzinfo=timezone.utc),
            "summary": summary,
            "_feed_source": feed_source,
        })
    return items


def fetch_feed(source, url):
    items = _parse_items(_download(url))
    for item in items:
        item["source"] = source
        item.pop("_feed_source", None)
    return items


def fetch_topic_items(topic, days=3):
    """Fetch a Google News search feed for an arbitrary user-supplied topic,
    attributing each item to its actual publisher (from the feed's <source>
    tag) so top_trending() can do the same cross-source comparison it does
    for the fixed categories."""
    query = urllib.parse.quote(f"{topic} when:{days}d")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    items = _parse_items(_download(url))
    for item in items:
        item["source"] = item.pop("_feed_source", None) or "Google News"
    return items


def keyword_match(item):
    text = (item["title"] + " " + item["summary"]).lower()
    return any(kw in text for kw in BIG_TECH_KEYWORDS)


def cluster_by_topic(items):
    """Group items across the 3 sources into topic clusters by shared
    named-entity overlap, so the same story is recognized even when each
    outlet words its headline differently."""
    items = sorted(items, key=lambda i: i["_sort_dt"], reverse=True)
    clusters = []  # [{"entities": set, "items": [...]}]
    for item in items:
        entities = extract_entities(item["title"])
        best, best_score = None, 0.0
        for c in clusters:
            score = jaccard(entities, c["entities"])
            if score > best_score:
                best, best_score = c, score
        if best is not None and entities and best_score >= 0.25:
            best["items"].append(item)
            best["entities"] |= entities
        else:
            clusters.append({"entities": set(entities), "items": [item]})
    return clusters


def top_trending(items, limit=TOP_N):
    """Rank topic clusters by how many distinct sources covered them
    (cross-source agreement = trending), then by story count, then recency.
    Falls back to plain recency when nothing overlaps across sources."""
    clusters = cluster_by_topic(items)

    def cluster_key(c):
        sources = {i["source"] for i in c["items"]}
        latest = max(i["_sort_dt"] for i in c["items"])
        return (len(sources), len(c["items"]), latest)

    clusters.sort(key=cluster_key, reverse=True)

    result = []
    for c in clusters[:limit]:
        c_items = sorted(c["items"], key=lambda i: i["_sort_dt"], reverse=True)
        top = c_items[0]
        result.append({
            "title": top["title"],
            "link": top["link"],
            "source": top["source"],
            "time_ago": top["time_ago"],
            "summary": top["summary"],
            "covered_by": sorted({i["source"] for i in c_items}),
        })
    return result


def build_category(name):
    sources = FEEDS[name]
    all_items = []
    errors = []
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {pool.submit(fetch_feed, src, url): src for src, url in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                all_items.extend(fut.result())
            except Exception as e:
                errors.append(f"{src}: {e}")

    if name == "tech":
        all_items = [i for i in all_items if keyword_match(i)]

    return top_trending(all_items), errors


def build_custom_topic(topic):
    try:
        items = fetch_topic_items(topic)
        return top_trending(items), []
    except Exception as e:
        return [], [f"{topic}: {e}"]


def build_digest(custom_topic=None):
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "categories": {},
        "custom_topic": None,
        "errors": [],
    }
    extra_workers = 1 if custom_topic else 0
    with ThreadPoolExecutor(max_workers=len(FEEDS) + extra_workers) as pool:
        futures = {pool.submit(build_category, name): ("category", name) for name in FEEDS}
        if custom_topic:
            futures[pool.submit(build_custom_topic, custom_topic)] = ("custom", custom_topic)

        for fut in as_completed(futures):
            kind, name = futures[fut]
            items, errors = fut.result()
            if kind == "category":
                result["categories"][name] = items
            else:
                result["custom_topic"] = {"topic": name, "items": items}
            result["errors"].extend(errors)
    return result
