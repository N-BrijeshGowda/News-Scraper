"""Download pictures to the local media/ folder.

Safety rules: only public http(s) hosts (no localhost or private networks, redirects included),
only real raster images (checked by file signature, SVG is refused), and a size cap.
"""
import hashlib
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

MEDIA_DIR = Path(__file__).parent / "media"
MAX_BYTES = 6 * 1024 * 1024
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsScraper/1.0)"}


def _ext(head):
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def is_public(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def fetch_image(url, referer=None, min_bytes=0):
    """Download one picture. Returns the saved file name, or None if it is unsafe or not an image."""
    try:
        headers = dict(HEADERS, Accept="image/*")
        if referer:
            headers["Referer"] = referer
        for _ in range(4):  # follow a few redirects by hand so each hop is checked
            if not is_public(url):
                return None
            r = requests.get(url, headers=headers, timeout=10, stream=True, allow_redirects=False)
            if r.is_redirect and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                r.close()
                continue
            break
        else:
            return None
        if r.status_code != 200:
            r.close()
            return None
        chunks, size = [], 0
        for chunk in r.iter_content(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                r.close()
                return None
            chunks.append(chunk)
        data = b"".join(chunks)
        ext = _ext(data[:16])
        if not ext or len(data) < max(min_bytes, 200):
            return None
        MEDIA_DIR.mkdir(exist_ok=True)
        name = hashlib.sha1(url.encode()).hexdigest()[:20] + ext
        path = MEDIA_DIR / name
        if not path.exists():
            path.write_bytes(data)
        return name
    except Exception:
        return None
