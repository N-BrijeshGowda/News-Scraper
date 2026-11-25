"""Scraping pipeline: phase 1 = priority sources, phase 2 = topic web search."""
import json
import re
import time
from datetime import date, datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

import media
from db import Item, Run, Session, Source, Topic

MAX_IMAGES = 5
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsScraper/1.0)"}
MAX_ARTICLES_PER_SOURCE = 15
MAX_RESULTS_PER_TOPIC = 10
DELAY = 1.0  # seconds between requests, be polite


MAX_HTML = 5 * 1024 * 1024
_robots = {}  # origin -> RobotFileParser or None (None = no usable robots.txt, allow)


def _get(url, timeout=20):
    """GET that only talks to public hosts: every redirect hop is re-checked, body is size-capped."""
    for _ in range(5):
        if not media.is_public(url):
            return None
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True, allow_redirects=False)
        if r.is_redirect and r.headers.get("Location"):
            url = urljoin(url, r.headers["Location"])
            r.close()
            continue
        return r
    return None


def allowed(url):
    """Respect the site's robots.txt (cached per site). If it can't be read, assume allowed."""
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    if origin not in _robots:
        rp = None
        try:
            r = _get(origin + "/robots.txt", timeout=10)
            if r is not None and r.status_code == 200:
                rp = RobotFileParser()
                rp.parse(r.text[:200000].splitlines())
            if r is not None:
                r.close()
        except Exception:
            rp = None
        _robots[origin] = rp
    rp = _robots[origin]
    return rp.can_fetch("NewsScraper", url) if rp else True


def fetch(url):
    try:
        if not allowed(url):
            return None
        r = _get(url)
        if r is None or r.status_code >= 400:
            return None
        body, size = [], 0
        for chunk in r.iter_content(65536):
            size += len(chunk)
            if size > MAX_HTML:
                r.close()
                return None
            body.append(chunk)
        r.encoding = r.encoding or r.apparent_encoding
        return b"".join(body).decode(r.encoding or "utf-8", errors="replace")
    except Exception:
        return None


def candidate_links(base_url, html, limit):
    """Prefer the site's RSS/Atom feed; fall back to same-site links with long anchor text."""
    soup = BeautifulSoup(html, "lxml")
    links = []
    feed = soup.find("link", rel="alternate", type=re.compile("rss|atom"))
    if feed and feed.get("href"):
        xml = fetch(urljoin(base_url, feed["href"]))  # fetched through the safe path, then parsed
        parsed = feedparser.parse(xml) if xml else None
        links = [e.link for e in parsed.entries if getattr(e, "link", None)] if parsed else []
    if not links:
        host = urlparse(base_url).netloc
        for a in soup.find_all("a", href=True):
            u = urljoin(base_url, a["href"]).split("#")[0]
            if urlparse(u).netloc == host and len(a.get_text(strip=True)) > 25 and u != base_url:
                links.append(u)
    return list(dict.fromkeys(links))[:limit]


def extract(html, url):
    data = trafilatura.bare_extraction(html, url=url, with_metadata=True)
    if not data:
        return None
    d = data if isinstance(data, dict) else data.as_dict()
    # drop extraction junk such as "unknown content item" and lone dashes
    text = "\n".join(l for l in (d.get("text") or "").splitlines()
                     if l.strip() not in ("-", "unknown content item")).strip()
    if len(text) < 200:
        return None
    published = str(d.get("date") or "")
    # A section or home page: no date, a one-level path, not much text. Not an article.
    if not published and len([x for x in urlparse(url).path.split("/") if x]) <= 1 and len(text) < 1500:
        return None
    return {
        "title": (d.get("title") or url).strip(),
        "published": published,
        "text": text,
        "excerpt": text[:500],
        "image_urls": find_images(html, url, d.get("image")),
    }


_SKIP = ("logo", "icon", "sprite", "avatar", "pixel", "tracking", "badge", "emoji", "placeholder", "blank", "banner-ad")


def find_images(html, url, main=None):
    """The page's main picture first, then pictures inside the article. At most MAX_IMAGES."""
    urls = []
    if main:
        urls.append(urljoin(url, str(main)))
    try:
        soup = BeautifulSoup(html, "lxml")
        if not urls:
            m = (soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"}))
            if m and m.get("content"):
                urls.append(urljoin(url, m["content"]))
        area = soup.find("article") or soup.find("main") or soup.body or soup
        for img in area.find_all("img"):
            src = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
            srcset = img.get("srcset") or img.get("data-srcset") or ""
            if srcset:  # take the largest candidate
                try:
                    best = max((c.strip().split() for c in srcset.split(",") if c.strip()),
                               key=lambda c: float(c[1][:-1]) if len(c) > 1 and c[1][:-1].replace(".", "", 1).isdigit() else 0)
                    src = best[0]
                except Exception:
                    pass
            if not src or src.startswith("data:"):
                continue
            w, h = str(img.get("width", "")), str(img.get("height", ""))
            if (w.isdigit() and int(w) < 200) or (h.isdigit() and int(h) < 120):
                continue
            full = urljoin(url, src)
            low = full.lower().split("?")[0]
            if low.endswith(".svg") or any(k in low for k in _SKIP):
                continue
            urls.append(full)
    except Exception:
        pass
    return list(dict.fromkeys(urls))[:MAX_IMAGES]


def download_images(urls, referer):
    """Download the pictures. First one is the main picture. Returns (main_name, [other_names])."""
    names = []
    for i, u in enumerate(urls):
        name = media.fetch_image(u, referer=referer, min_bytes=0 if i == 0 else 8000)
        if name and name not in names:
            names.append(name)
    return (names[0] if names else ""), names[1:]


def search_topic(topic, limit):
    from ddgs import DDGS
    try:
        return [r["url"] for r in DDGS().news(topic, max_results=limit) if r.get("url")]
    except Exception:
        return []


def run_scrape(run_id):
    """Execute one full run, updating the Run row so the UI can show live progress."""
    today = date.today().isoformat()
    with Session() as s:
        sources = s.query(Source).filter_by(enabled=True).order_by(Source.priority.desc(), Source.id).all()
        topics = [t.name for t in s.query(Topic).filter_by(enabled=True).all()]
        seen = {l for (l,) in s.query(Item.link).all()}
        seq = s.query(Item).filter_by(run_date=today).count()

    def progress(**kw):
        with Session() as s:
            run = s.get(Run, run_id)
            for k, v in kw.items():
                setattr(run, k, v)
            s.commit()

    def save(link, stype, src, topic, data):
        nonlocal seq
        seq += 1
        main_img, more_imgs = download_images(data.get("image_urls", []), link)
        with Session() as s:
            s.add(Item(run_date=today, seq=seq, source_type=stype, source_url=src, topic=topic,
                       title=data["title"], link=link, published=data["published"],
                       excerpt=data["excerpt"], text=data["text"],
                       image=main_img, images=json.dumps(more_imgs) if more_imgs else ""))
            run = s.get(Run, run_id)
            run.added += 1
            s.commit()

    def process(link, stype, src, topic=""):
        if link in seen:
            return
        seen.add(link)
        try:  # one bad article must not abort the whole run
            html = fetch(link)
            time.sleep(DELAY)
            data = extract(html, link) if html else None
            if data:
                save(link, stype, src, topic, data)
        except Exception:
            pass

    try:
        progress(total=len(sources) + len(topics), done=0)
        done = 0
        # Phase 1: priority sources (priority-flagged first, then the rest)
        for src in sources:
            progress(phase=f"Source: {src.url}")
            html = fetch(src.url)
            if html:
                for link in candidate_links(src.url, html, MAX_ARTICLES_PER_SOURCE):
                    process(link, "priority", src.url)
            done += 1
            progress(done=done)
        # Phase 2: topic search
        for topic in topics:
            progress(phase=f"Search: {topic}")
            for link in search_topic(topic, MAX_RESULTS_PER_TOPIC):
                process(link, "search", urlparse(link).netloc, topic)
            done += 1
            progress(done=done)
        progress(status="done", phase="Finished", finished=datetime.now())
    except Exception as e:
        progress(status="error", message=str(e), finished=datetime.now())
