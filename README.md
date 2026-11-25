# News Scraper

A password-protected web app that collects news once a day and keeps it in one organised daily briefing for a team.

## About

News Scraper is an internal tool built for the WODO team to follow the news without checking sites by hand. Each day it reads a team-managed list of websites and topics, saves what it finds as full articles with pictures, and presents them as a briefing that can be read in the browser or exported for other tools.

- **Who it is for:** a small team sharing one login; no technical skills needed to add sources.
- **What it is not:** it does not summarise or rewrite articles, and it does not scrape sites that forbid it (for example LinkedIn, which is paste-only).
- **Status:** v1 is built and tested locally; deployment to the company VPS is the next step.
- **More:** see [PRD.md](PRD.md) for the goals and requirements.

It scrapes a list of priority websites first, then searches the web for your topics, then stores everything by day. You can also paste LinkedIn-style posts by hand and add YouTube links (with transcripts and graph screenshots).

## Features

- **Priority sources first:** your URL list is scraped before anything else (RSS feed if the site has one, otherwise article links on the page).
- **Topic search:** each topic is searched with DuckDuckGo news (free, no API key).
- **Daily briefing:** items are grouped by source, with a spotlight of top stories and a full-window reader.
- **Images:** article pictures are downloaded and stored locally, not hot-linked.
- **Paste a post:** for content that can't be scraped (for example LinkedIn).
- **YouTube:** details, thumbnail, embedded player, timestamped transcript, and optional graph/slide screenshots (needs the video file).
- **Manage everything in the UI:** add URLs by paste or `.txt`/`.csv` upload, set priority, pause, delete.
- **History:** a 14-week heat grid and a table of runs.
- **Export:** any day as JSON or CSV at `/export/YYYY-MM-DD.json` or `.csv`.
- **Daily timer:** runs automatically at 07:00 (configurable) and can also be started with **Run now**.
- **Safe by default:** shared login, CSRF protection, Content-Security-Policy, `robots.txt` respected, no requests to private network addresses.

## Tech stack

Python 3.14, Flask, SQLAlchemy (SQLite, Postgres possible), requests, BeautifulSoup + lxml, trafilatura, feedparser, ddgs, APScheduler, youtube-transcript-api, imageio-ffmpeg + numpy, optional anthropic and yt-dlp. gunicorn behind Nginx in production.

## Quick start (local, Windows or any OS)

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

On first run a random password is printed in the terminal (username `wodo`). Change it any time:

```bash
python set_password.py
```

Then: **Add new scraper** -> paste URLs and topics -> **Run now**.

## Configuration

Settings are environment variables. They can be put in `~/.news-scraper.env` (recommended, keep it outside synced folders) or a `.env` file next to `app.py`. A variable already set in the OS wins. See `.env.example`.

| Variable | Purpose | Default |
|---|---|---|
| `RUN_AT` | Time of the daily scrape (`HH:MM`) | `07:00` |
| `SCHEDULE_TZ` | Time zone for the timer, e.g. `Asia/Kolkata` | server time |
| `RUN_SCHEDULER` | `1` starts the daily timer under gunicorn | off |
| `ANTHROPIC_API_KEY` | Enables the optional AI check on video screenshots | off |
| `VISION_MODEL` | Model for that check | `claude-haiku-5-5` |
| `ALLOW_YOUTUBE_DOWNLOAD` | `1` lets the app download videos itself (breaks YouTube's terms) | off |
| `DATABASE_URL` | Database URL | local SQLite file |
| `APP_USERNAME` / `APP_PASSWORD` | Override the stored login | stored in `credentials.json` |
| `SECRET_KEY` | Flask session key | generated `secret.key` |
| `COOKIE_SECURE` | `1` when served over https | off |
| `PORT` | gunicorn port | `8000` |

**Never commit real keys.** `.env`, `credentials.json`, `secret.key`, `news.db`, `media/` and `uploads/` are already in `.gitignore`.

## Project layout

```
app.py              Flask routes, helpers, daily scheduler
scraper.py          scraping pipeline (safe fetch, robots.txt, extraction, images)
db.py               models (Source, Topic, Item, Run) and small migration
media.py            safe image downloader (public hosts only, size cap, signature check)
youtube.py          YouTube link parsing and details
videos.py           transcript, slide/graph detection, optional AI check
auth.py             shared team login
envfile.py          loads settings from env files
templates/, static/ pages, styles and scripts
tests/              unit and route tests
deploy/             systemd service and backup script
DEPLOY.md           step-by-step server setup (Hostinger VPS + CloudPanel)
```

## Tests

```bash
python -m unittest discover -s tests
```

Tests use a throwaway database.

## Deployment

See [DEPLOY.md](DEPLOY.md). In short: gunicorn with **one worker** (the daily timer and background jobs live inside the app process), systemd to keep it running, Nginx/CloudPanel for https, and `deploy/backup.sh` for daily backups.

## Known limits

- No JavaScript rendering, so sites that load content only in the browser return little.
- YouTube videos without captions get no transcript, and YouTube may block transcript requests from datacenter IPs.
- Screenshots of graphs need the video file (upload it with the link).
- Must run with a single worker process.
- Postgres has not been tested.

## Documentation

- [PRD.md](PRD.md): product requirements
- [DEPLOY.md](DEPLOY.md): deployment guide
