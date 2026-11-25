"""YouTube links: read a video's details (no API key, no video download) so it can be shown and played on the site."""
import json
import re
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsScraper/1.0)", "Accept-Language": "en"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}


def parse_id(url):
    """Return the 11 character video id from any common YouTube link, or None."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    if (p.hostname or "").lower() not in HOSTS:
        return None
    if p.hostname.lower().endswith("youtu.be"):
        vid = p.path.strip("/").split("/")[0]
    elif p.path == "/watch":
        vid = (parse_qs(p.query).get("v") or [""])[0]
    else:
        parts = [x for x in p.path.split("/") if x]
        vid = parts[1] if len(parts) > 1 and parts[0] in ("shorts", "embed", "live", "v") else ""
    return vid if ID_RE.match(vid) else None


def watch_url(vid):
    return f"https://www.youtube.com/watch?v={vid}"


def fetch_meta(vid):
    """Title, channel, description and date for a video. Returns None if it can't be found."""
    try:
        r = requests.get("https://www.youtube.com/oembed", params={"url": watch_url(vid), "format": "json"},
                         headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        o = r.json()
    except Exception:
        return None
    meta = {"title": (o.get("title") or "YouTube video").strip(),
            "channel": (o.get("author_name") or "YouTube").strip(),
            "description": "", "published": ""}
    try:  # the watch page has the full description and the upload date
        page = requests.get(watch_url(vid), headers=HEADERS, cookies={"CONSENT": "YES+1"}, timeout=10).text
        m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', page)
        if m:
            meta["description"] = json.loads('"' + m.group(1) + '"').strip()
        soup = BeautifulSoup(page, "lxml")
        if not meta["description"]:
            og = soup.find("meta", property="og:description")
            meta["description"] = (og.get("content") if og else "") or ""
        d = soup.find("meta", itemprop="datePublished") or soup.find("meta", itemprop="uploadDate")
        if d and d.get("content"):
            meta["published"] = d["content"][:10]
    except Exception:
        pass
    return meta
