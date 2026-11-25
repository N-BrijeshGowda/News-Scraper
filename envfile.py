"""Load settings (such as ANTHROPIC_API_KEY) from a simple KEY=value file.

Looked up in this order, and a variable already set in Windows always wins:
  1. ~/.news-scraper.env   (your user folder, NOT synced by OneDrive: recommended for secrets)
  2. .env                  (next to app.py, only for non-secret settings)
Import this module before anything that reads os.environ.
"""
import os
from pathlib import Path


def load():
    for path in (Path.home() / ".news-scraper.env", Path(__file__).parent / ".env"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


load()
