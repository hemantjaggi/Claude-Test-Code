#!/usr/bin/env python3
"""
Local dev server for the Daily Newsfeed app.
Serves newsfeed.html and aggregates live RSS feeds into 5 categories
(AI, World, Tech Companies, Sports, India) via news_digest.build_digest().

For always-on / on-the-go access (computer off, phone anywhere), see
generate_digest.py + .github/workflows/newsfeed.yml, which publish a
static version of this to GitHub Pages instead.

Run: python3 newsfeed_server.py
Then open: http://localhost:8766
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from news_digest import build_digest

PORT = 8766
DIR = os.path.dirname(os.path.abspath(__file__))


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        if int(args[1]) >= 400:
            print(f"  [{args[1]}] {args[0]}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/news":
            topic = parse_qs(parsed.query).get("topic", [""])[0].strip() or None
            try:
                digest = build_digest(custom_topic=topic)
                body = json.dumps(digest).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(500)
                self._cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if path == "/":
            path = "/newsfeed.html"

        filepath = os.path.join(DIR, path.lstrip("/"))
        if not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            return

        ext = os.path.splitext(filepath)[1].lstrip(".")
        mime = {"html": "text/html", "js": "text/javascript", "css": "text/css"}.get(ext, "application/octet-stream")

        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


if __name__ == "__main__":
    httpd = HTTPServer(("localhost", PORT), Handler)
    print(f"Server running at http://localhost:{PORT}")
    print(f"Open: http://localhost:{PORT}/newsfeed.html")
    print("Press Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
