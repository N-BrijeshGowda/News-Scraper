import envfile  # noqa: F401  (loads settings before anything reads them)
import csv
import hashlib
import io
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, send_from_directory, session, url_for)
from sqlalchemy import func
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import json
import media
import scraper
import uuid
import videos
import youtube
from pathlib import Path
from db import Item, Run, Session, Source, Topic, init_db

app = Flask(__name__)
# Behind CloudPanel's Nginx: trust one proxy hop so we see the real visitor address and https.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = auth.secret_key()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
                  MAX_CONTENT_LENGTH=2 * 1024 * 1024 * 1024)  # room for an uploaded video file
UPLOAD_DIR = Path(__file__).parent / "uploads"
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
init_db()
auth._load()  # creates the first-run login if needed
# A restart kills background threads, so anything still marked "running" is stale and would block new runs.
with Session() as _s:
    for _r in _s.query(Run).filter_by(status="running"):
        _r.status, _r.message, _r.finished = "error", "Interrupted by a restart.", datetime.now()
    for _i in _s.query(Item).filter(Item.proc.in_(["queued", "transcript", "frames", "vision"])):
        _i.proc, _i.procnote = "error", "Interrupted by a restart. Use reprocess to try again."
    _s.commit()
_lock = threading.Lock()
_fails = {}  # ip -> (count, window_start)


# ---------- helpers ----------
def host(u):
    h = urlparse(u).netloc
    return h[4:] if h.startswith("www.") else h


def longdate(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%A %d %B %Y")
    except (TypeError, ValueError):
        return d or ""


def srclabel(it):
    """Human label for where an item came from."""
    if it.source_type == "priority":
        return host(it.source_url)
    return it.topic if it.source_type == "search" else it.source_url


def href(link):
    """Only real web links are clickable (pasted posts may have none)."""
    return link if (link or "").startswith(("http://", "https://")) else "#"


def mediaurl(name):
    return url_for("media_file", name=name) if name else ""


def more_images(it):
    try:
        return [mediaurl(n) for n in json.loads(it.images or "[]")]
    except ValueError:
        return []


app.jinja_env.filters["mediaurl"] = mediaurl
app.jinja_env.filters["more_images"] = more_images
app.jinja_env.filters["srclabel"] = srclabel
app.jinja_env.filters["href"] = href
app.jinja_env.filters["host"] = host
app.jinja_env.filters["longdate"] = longdate


def current_run():
    with Session() as s:
        return s.query(Run).filter_by(status="running").first()


def start_run():
    """Start a background run unless one is already going. Returns the run id or None."""
    with _lock:
        if current_run():
            return None
        with Session() as s:
            run = Run()
            s.add(run)
            s.commit()
            run_id = run.id
        threading.Thread(target=scraper.run_scrape, args=(run_id,), daemon=True).start()
        return run_id


def parse_urls(raw):
    urls = []
    for tok in re.split(r"[\s,;]+", raw):
        tok = tok.strip().strip('"\'')
        if not tok:
            continue
        if not tok.startswith(("http://", "https://")):
            tok = "https://" + tok
        if "." in tok:
            urls.append(tok)
    return list(dict.fromkeys(urls))


def safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else url_for("home")


# ---------- auth + CSRF ----------
@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return {"csrf_token": session["csrf"], "running": current_run() is not None,
            "csp_nonce": g.csp_nonce}


@app.before_request
def guard():
    g.csp_nonce = secrets.token_urlsafe(16)
    # Check the login first, so an anonymous visitor can never make us read a large upload.
    if request.endpoint not in ("login", "static", "healthz") and not session.get("auth"):
        return redirect(url_for("login", next=request.full_path if request.method == "GET" else None))
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
            abort(400)


@app.after_request
def headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    nonce = getattr(g, "csp_nonce", "")
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; font-src https://cdn.jsdelivr.net; "
        "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
        "frame-src https://www.youtube-nocookie.com; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'")
    return resp


@app.get("/healthz")
def healthz():
    """For uptime checks: reports ok if the database answers. Reveals nothing else."""
    try:
        with Session() as s:
            s.query(Run).first()
        return jsonify(ok=True)
    except Exception:
        return jsonify(ok=False), 503


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("auth"):
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        count, since = _fails.get(ip, (0, time.time()))
        if time.time() - since > 300:
            count, since = 0, time.time()
        if count >= 5:
            error = "Too many attempts. Wait a few minutes and try again."
        elif auth.check(request.form.get("username", ""), request.form.get("password", "")):
            _fails.pop(ip, None)
            session.clear()
            session["auth"] = True
            session.permanent = True
            return redirect(safe_next(request.args.get("next", "").rstrip("?")))
        else:
            _fails[ip] = (count + 1, since)
            error = "Wrong username or password."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- pages ----------
@app.route("/")
def home():
    day = request.args.get("day", "")
    with Session() as s:
        days = [d for (d,) in s.query(Item.run_date).distinct().order_by(Item.run_date.desc()).all()]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or day not in days:
            day = days[0] if days else None
        items = s.query(Item).filter_by(run_date=day).order_by(Item.seq).all() if day else []
        sources = s.query(Source).filter_by(enabled=True).order_by(Source.priority.desc(), Source.id).all()
        n_topics = s.query(Topic).filter_by(enabled=True).count()
        last_run = s.query(Run).order_by(Run.id.desc()).first()

    # Group by where the item came from: priority sources first, then topic searches.
    pri, search, manual, videos = {}, {}, {}, {}
    for src in sources:
        pri[src.url] = {"label": host(src.url), "sub": src.url, "items": []}
    for it in items:
        if it.source_type == "priority":
            g = pri.setdefault(it.source_url, {"label": host(it.source_url), "sub": it.source_url, "items": []})
        elif it.source_type == "video":
            g = videos.setdefault(it.source_url, {"label": it.source_url, "sub": "YouTube channel", "items": []})
        elif it.source_type == "manual":
            g = manual.setdefault(it.source_url, {"label": it.source_url, "sub": "Pasted by the team", "items": []})
        else:
            g = search.setdefault(it.topic or "Other", {"label": it.topic or "Other", "sub": "Web search", "items": []})
        g["items"].append(it)
    pri_groups = sorted(pri.values(), key=lambda g: -len(g["items"]))
    search_groups = sorted(search.values(), key=lambda g: -len(g["items"]))
    man_groups = sorted(manual.values(), key=lambda g: -len(g["items"]))
    vid_groups = sorted(videos.values(), key=lambda g: -len(g["items"]))

    # Front page: one lead story, a few more, and a ticker of every headline.
    # Spotlight rotation: the longest stories, at most 2 per source so it stays varied.
    leads, per_src = [], {}
    for it in sorted(items, key=lambda i: (bool(i.image), len(i.text or "")), reverse=True):
        label = srclabel(it)
        if per_src.get(label, 0) < 2:
            leads.append(it)
            per_src[label] = per_src.get(label, 0) + 1
        if len(leads) == 6:
            break
    lead = leads[0] if leads else None
    also = []
    ticker = [i for i in items][:24]

    # Volume over the 14 days ending on the viewed day.
    end = datetime.strptime(day, "%Y-%m-%d").date() if day else datetime.now().date()
    with Session() as s:
        per_day = dict(s.query(Item.run_date, func.count()).group_by(Item.run_date).all())
    activity = []
    for k in range(13, -1, -1):
        d = end - timedelta(days=k)
        activity.append({"day": d.isoformat(), "n": per_day.get(d.isoformat(), 0),
                         "wd": d.strftime("%a")[:2], "d": d.day})
    peak = max([a["n"] for a in activity] + [1])

    idx = days.index(day) if day in days else -1
    return render_template(
        "home.html", day=day, days=days, items=items,
        lead=lead, leads=leads, also=also, ticker=ticker, activity=activity, peak=peak,
        newer=days[idx - 1] if idx > 0 else None,
        older=days[idx + 1] if 0 <= idx < len(days) - 1 else None,
        pri_groups=pri_groups, search_groups=search_groups, man_groups=man_groups, vid_groups=vid_groups,
        n_manual=sum(len(g["items"]) for g in man_groups) + sum(len(g["items"]) for g in vid_groups),
        n_pri=sum(len(g["items"]) for g in pri_groups),
        n_search=sum(len(g["items"]) for g in search_groups),
        reporting=sum(1 for g in pri_groups if g["items"]),
        n_sources=len(sources), n_topics=n_topics, last_run=last_run,
        today=datetime.now().strftime("%Y-%m-%d"))


@app.route("/add")
def add():
    with Session() as s:
        n_src = s.query(Source).count()
        n_top = s.query(Topic).count()
        recent_src = s.query(Source).order_by(Source.id.desc()).limit(4).all()
        recent_top = s.query(Topic).order_by(Topic.id.desc()).limit(4).all()
    return render_template("add.html", n_src=n_src, n_top=n_top, yt_download=videos.ALLOW_DOWNLOAD,
                           recent_src=recent_src, recent_top=recent_top)


@app.route("/manage")
def manage():
    with Session() as s:
        sources = s.query(Source).order_by(Source.priority.desc(), Source.id).all()
        topics = s.query(Topic).order_by(Topic.id).all()
        src_counts = dict(s.query(Item.source_url, func.count()).filter_by(source_type="priority")
                          .group_by(Item.source_url).all())
        topic_counts = dict(s.query(Item.topic, func.count()).filter_by(source_type="search")
                            .group_by(Item.topic).all())
    return render_template("manage.html", sources=sources, topics=topics,
                           src_counts=src_counts, topic_counts=topic_counts)


@app.route("/runs")
def runs():
    with Session() as s:
        rows = s.query(Run).order_by(Run.id.desc()).limit(40).all()
        total_added = s.query(func.coalesce(func.sum(Run.added), 0)).scalar()
        n_runs = s.query(Run).count()
        n_ok = s.query(Run).filter_by(status="done").count()
        per_day = dict(s.query(Item.run_date, func.count()).group_by(Item.run_date).all())

    # 14-week calendar heat grid, Monday first, one column per week.
    weeks, today = 14, datetime.now().date()
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    peak = max(list(per_day.values()) + [1])
    heat = []
    for w in range(weeks):
        for dow in range(7):
            d = start + timedelta(days=7 * w + dow)
            n = per_day.get(d.isoformat(), 0)
            level = 0 if n == 0 else min(4, 1 + int(3 * (n - 1) / max(peak - 1, 1)))
            heat.append({"day": d.isoformat(), "n": n, "level": level, "future": d > today})
    return render_template("runs.html", runs=rows, total_added=total_added, heat=heat,
                           n_runs=n_runs, ok_rate=round(100 * n_ok / n_runs) if n_runs else 0)


# ---------- actions ----------
@app.post("/sources/add")
def add_sources():
    raw = request.form.get("urls", "")
    f = request.files.get("file")
    if f and f.filename:
        raw += "\n" + f.read().decode("utf-8-sig", errors="ignore")
    priority = request.form.get("priority") == "on"
    urls = parse_urls(raw)
    added = 0
    with Session() as s:
        have = {u for (u,) in s.query(Source.url).all()}
        for u in urls:
            if u not in have:
                s.add(Source(url=u, priority=priority))
                added += 1
        s.commit()
    if not urls:
        flash("No valid URLs found. Paste one per line, or upload a .txt/.csv file.", "error")
    else:
        flash(f"Added {added} URL{'s' if added != 1 else ''}"
              + (f" ({len(urls) - added} already existed)." if len(urls) > added else "."), "ok")
    if request.form.get("run_now") == "on" and added:
        start_run()
    return redirect(url_for("add"))


@app.post("/topics/add")
def add_topics():
    names = [t.strip() for t in re.split(r"[\n,;]+", request.form.get("topics", "")) if t.strip()]
    added = 0
    with Session() as s:
        have = {n for (n,) in s.query(Topic.name).all()}
        for n in dict.fromkeys(names):
            if n not in have:
                s.add(Topic(name=n))
                added += 1
        s.commit()
    if not names:
        flash("Enter at least one topic.", "error")
    else:
        flash(f"Added {added} topic{'s' if added != 1 else ''}.", "ok")
    if request.form.get("run_now") == "on" and added:
        start_run()
    return redirect(url_for("add") + "#pane-topics")


@app.route("/media/<path:name>")
def media_file(name):
    """Downloaded article pictures, served only to logged-in users."""
    return send_from_directory(media.MEDIA_DIR, name, max_age=86400)


@app.get("/items/<int:item_id>/video")
def video_details(item_id):
    """Transcript paragraphs and screenshots for the reader panel."""
    with Session() as s:
        it = s.get(Item, item_id)
        if not it or it.source_type != "video":
            abort(404)
        try:
            rows = json.loads(it.transcript or "[]")
            shots = json.loads(it.shots or "[]")
        except ValueError:
            rows, shots = [], []
        return jsonify(proc=it.proc or "", label=videos.STATES.get(it.proc or "", ""), note=it.procnote or "",
                       segments=videos.group_lines(rows),
                       shots=[{"t": x["t"], "url": mediaurl(x["file"]), "kind": x.get("kind", ""),
                               "caption": x.get("caption", "")} for x in shots])


@app.post("/videos/<int:item_id>/reprocess")
def reprocess_video(item_id):
    with Session() as s:
        it = s.get(Item, item_id)
        if not it or it.source_type != "video" or (it.proc or "") in ("queued", "transcript", "frames", "vision"):
            abort(404)
        vid = it.video_id
    threading.Thread(target=videos.process_video, args=(item_id, vid, None), daemon=True).start()
    return jsonify(ok=True)


@app.post("/videos/add")
def add_videos():
    """Save details, thumbnail and an embedded player for pasted YouTube links."""
    raw = [u for u in re.split(r"[\s,;]+", request.form.get("urls", "")) if u]
    if not raw:
        flash("Paste at least one YouTube link.", "error")
        return redirect(url_for("add") + "#pane-video")
    today = datetime.now().strftime("%Y-%m-%d")
    added = dup = bad = missing = 0
    links = list(dict.fromkeys(raw))[:5]
    upload_path = None
    up = request.files.get("file")
    if up and up.filename:
        ext = Path(up.filename).suffix.lower()
        if ext not in VIDEO_EXTS:
            flash("Upload a video file (.mp4, .mov, .webm, .mkv, .avi or .m4v).", "error")
            return redirect(url_for("add") + "#pane-video")
        if len(links) != 1:
            flash("Attach a video file only when you paste exactly one link.", "error")
            return redirect(url_for("add") + "#pane-video")
        UPLOAD_DIR.mkdir(exist_ok=True)
        upload_path = UPLOAD_DIR / (uuid.uuid4().hex + ext)
        up.save(upload_path)
    for u in links:
        vid = youtube.parse_id(u)
        if not vid:
            bad += 1
            continue
        link = youtube.watch_url(vid)
        with Session() as s:
            if s.query(Item).filter_by(link=link).first():
                dup += 1
                continue
        meta = youtube.fetch_meta(vid)
        if not meta:
            missing += 1
            continue
        thumb = (media.fetch_image(f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg")
                 or media.fetch_image(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"))
        desc = meta["description"] or meta["title"]
        with Session() as s:
            seq = s.query(Item).filter_by(run_date=today).count() + 1
            item = Item(run_date=today, seq=seq, source_type="video", source_url=meta["channel"],
                        title=meta["title"], link=link, published=meta["published"], excerpt=desc[:500],
                        text=desc, image=thumb or "", video_id=vid, proc="queued")
            s.add(item)
            s.commit()
            item_id = item.id
        threading.Thread(target=videos.process_video, args=(item_id, vid, str(upload_path) if upload_path else None),
                         daemon=True).start()
        upload_path = None  # the background job now owns (and deletes) the file
        added += 1
    if upload_path:  # link was rejected, so the file is not needed
        upload_path.unlink(missing_ok=True)
    parts = []
    if added:
        parts.append(f"Saved {added} video{'s' if added != 1 else ''} to today's briefing. "
                     "The transcript and screenshots are being prepared; open the video on the Briefing page to watch the progress.")
    if dup:
        parts.append(f"{dup} already saved.")
    if bad:
        parts.append(f"{bad} not a YouTube video link.")
    if missing:
        parts.append(f"{missing} could not be found (private or removed?).")
    flash(" ".join(parts), "ok" if added else "error")
    return redirect(url_for("add") + "#pane-video")


@app.post("/posts/add")
def add_post():
    """Save a post the team pasted by hand (e.g. from LinkedIn) into today's briefing."""
    text = request.form.get("text", "").strip()
    link = request.form.get("link", "").strip()
    author = request.form.get("author", "").strip()[:120] or "LinkedIn"
    published = request.form.get("published", "").strip()[:32]
    if len(text) < 20:
        flash("Paste the post text first (at least a sentence).", "error")
        return redirect(url_for("add") + "#pane-post")
    if len(text) > 20000:
        flash("That post is too long (limit 20,000 characters).", "error")
        return redirect(url_for("add") + "#pane-post")
    if link and not link.startswith(("http://", "https://")):
        flash("The link must start with http:// or https://", "error")
        return redirect(url_for("add") + "#pane-post")
    key = link or "paste:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    first_line = text.splitlines()[0].strip()
    title = first_line if len(first_line) <= 120 else first_line[:117] + "..."
    today = datetime.now().strftime("%Y-%m-%d")
    with Session() as s:
        if s.query(Item).filter_by(link=key).first():
            flash("This post is already saved.", "error")
        else:
            seq = s.query(Item).filter_by(run_date=today).count() + 1
            s.add(Item(run_date=today, seq=seq, source_type="manual", source_url=author,
                       title=title, link=key, published=published, excerpt=text[:500], text=text))
            s.commit()
            flash("Saved to today's briefing.", "ok")
    return redirect(url_for("add") + "#pane-post")


@app.post("/<kind>/<int:item_id>/<action>")
def modify(kind, item_id, action):
    model = {"sources": Source, "topics": Topic}.get(kind)
    if not model or action not in ("toggle", "priority", "delete"):
        abort(404)
    with Session() as s:
        obj = s.get(model, item_id)
        if obj:
            if action == "delete":
                s.delete(obj)
            elif action == "toggle":
                obj.enabled = not obj.enabled
            elif action == "priority" and kind == "sources":
                obj.priority = not obj.priority
            s.commit()
    return redirect(url_for("manage"))


@app.post("/start")
def start():
    if start_run() is None:
        flash("A scrape is already running.", "error")
    return redirect(request.form.get("back") if (request.form.get("back") or "").startswith("/") else url_for("home"))


@app.get("/status")
def status():
    with Session() as s:
        run = s.query(Run).order_by(Run.id.desc()).first()
        if not run:
            return jsonify(status="idle")
        return jsonify(status=run.status, phase=run.phase, total=run.total,
                       done=run.done, added=run.added, message=run.message)


@app.route("/day/<day>")
def day_redirect(day):
    return redirect(url_for("home", day=day))


@app.route("/export/<day>.<fmt>")
def export(day, fmt):
    with Session() as s:
        items = s.query(Item).filter_by(run_date=day).order_by(Item.seq).all()
    if not items or fmt not in ("json", "csv"):
        abort(404)
    cols = ["seq", "run_date", "source_type", "source_url", "topic", "title", "link",
            "published", "excerpt", "text", "image", "video_id"]
    rows = [{c: getattr(i, c) for c in cols} for i in items]
    if fmt == "json":
        return jsonify(rows)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={day}.csv"})


_scheduler = None


def ensure_scheduler():
    """Start the daily 07:00 timer once. SCHEDULE_TZ (for example Asia/Kolkata) sets its time zone."""
    global _scheduler
    if _scheduler is None:
        hour, minute = os.environ.get("RUN_AT", "07:00").split(":")
        _scheduler = BackgroundScheduler(timezone=os.environ.get("SCHEDULE_TZ") or None)
        # If the server was down at the scheduled time, still run within the next hour.
        _scheduler.add_job(start_run, "cron", hour=int(hour), minute=int(minute),
                           misfire_grace_time=3600, coalesce=True)
        _scheduler.start()
    return _scheduler


if os.environ.get("RUN_SCHEDULER") == "1":  # production: gunicorn imports this file once (1 worker)
    ensure_scheduler()

if __name__ == "__main__":
    ensure_scheduler()
    app.run(port=5000)
