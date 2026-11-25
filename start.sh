#!/usr/bin/env bash
# Starts the News Scraper on the server. Run from the project folder (the systemd service does this).
cd "$(dirname "$0")" || exit 1
export RUN_SCHEDULER=1
export COOKIE_SECURE=1
export MALLOC_ARENA_MAX=2   # stops Python's memory use from creeping up with threads
exec .venv/bin/gunicorn -c gunicorn.conf.py app:app
