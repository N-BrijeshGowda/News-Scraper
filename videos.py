"""Video pipeline for pasted YouTube links.

Phase 1  transcript with timestamps (YouTube captions, no video download needed)
Phase 2  find slide/graph moments in the video file and save screenshots
Phase 3  optional AI check (Claude vision) that keeps real charts and writes a caption

The video FILE is needed only for phase 2. It comes from an upload, or from a YouTube download
when ALLOW_YOUTUBE_DOWNLOAD=1 is set (downloading breaks YouTube's terms, so it is off by default).
"""
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

import media
import youtube
from db import Item, Session

MAX_MINUTES = 60          # only the first hour of a video is analysed
SAMPLE_EVERY = 5          # seconds between sampled frames
W, H = 320, 180           # size of the small grey frames used to spot slide changes
MAX_SHOTS = 12
ALLOW_DOWNLOAD = os.environ.get("ALLOW_YOUTUBE_DOWNLOAD") == "1"
MODEL = os.environ.get("VISION_MODEL", "claude-haiku-5-5")
CUES = re.compile(r"\b(chart|graph|slide|table|diagram|figure|plot|axis|as you can see|you can see here|"
                  r"look at this|on the screen|trend|percent|per cent)\b", re.I)
_gate = threading.Semaphore(1)  # one video at a time, ffmpeg is heavy

STATES = {"queued": "Waiting in line", "transcript": "Reading the transcript",
          "frames": "Looking for graphs and slides", "vision": "Checking screenshots with AI",
          "done": "Done"}


def set_state(item_id, proc, note=None, **fields):
    with Session() as s:
        it = s.get(Item, item_id)
        if not it:
            return
        it.proc = proc
        if note is not None:
            it.procnote = note
        for k, v in fields.items():
            setattr(it, k, v)
        s.commit()


# ---------- phase 1: transcript ----------
def fetch_transcript(vid):
    """Caption lines [{t, text}] or (None, reason)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None, "The transcript library is not installed (pip install youtube-transcript-api)."
    try:
        try:
            api = YouTubeTranscriptApi()
            tl = api.list(vid)
            try:
                t = tl.find_manually_created_transcript(["en"])
            except Exception:
                try:
                    t = tl.find_generated_transcript(["en"])
                except Exception:
                    t = next(iter(tl))
            raw = t.fetch()
            rows = [{"t": float(getattr(r, "start", None) if not isinstance(r, dict) else r["start"]),
                     "text": getattr(r, "text", None) if not isinstance(r, dict) else r["text"]} for r in raw]
        except AttributeError:  # older library version
            raw = YouTubeTranscriptApi.get_transcript(vid, languages=["en"])
            rows = [{"t": float(r["start"]), "text": r["text"]} for r in raw]
    except Exception as e:
        name = type(e).__name__
        if "Disabled" in name or "NoTranscript" in name:
            return None, "This video has no captions, so there is no transcript."
        return None, f"Could not fetch the transcript ({name})."
    clean = []
    for r in rows:
        txt = html.unescape(re.sub(r"\s+", " ", str(r["text"]))).strip()
        if txt:
            clean.append({"t": round(r["t"], 1), "text": txt})
    return (clean or None), (None if clean else "The transcript was empty.")


def group_lines(rows, window=25):
    """Join short caption lines into readable paragraphs of about `window` seconds."""
    out, cur = [], None
    for r in rows:
        if cur is None or r["t"] - cur["t"] >= window:
            cur = {"t": int(r["t"]), "text": r["text"]}
            out.append(cur)
        else:
            cur["text"] += " " + r["text"]
    return out


# ---------- phase 2: find slide / graph moments ----------
def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def sample_diffs(video):
    """Streams small grey frames from ffmpeg and keeps only how much each differs from the one before.
    Memory stays flat (two tiny frames) however long the video is."""
    exe = ffmpeg_exe()
    cmd = [exe, "-v", "error", "-threads", "1", "-t", str(MAX_MINUTES * 60), "-i", str(video),
           "-vf", f"fps=1/{SAMPLE_EVERY},scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size, diffs, prev = W * H, [], None
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            cur = np.frombuffer(buf, dtype=np.uint8).astype(np.int16)
            diffs.append(0.0 if prev is None else float(np.abs(cur - prev).mean()))
            prev = cur
    finally:
        p.stdout.close()
        p.wait()
    return diffs


def cue_score(t, rows):
    n = sum(1 for r in rows if t - 10 <= r["t"] <= t + 20 and CUES.search(r["text"]))
    return min(n, 3)


def pick_moments(diffs, rows):
    """Moments where the picture changes sharply and then holds still (a new slide or chart),
    plus moments where the speaker points at something on screen."""
    cands = []
    for i in range(1, len(diffs)):
        settles = i + 1 >= len(diffs) or diffs[i + 1] < 3.0
        if diffs[i] >= 10 and settles:
            t = i * SAMPLE_EVERY + 1
            cands.append({"t": t, "score": cue_score(t, rows), "diff": diffs[i]})
    for r in rows:  # speaker cue with no slide change nearby
        if cue_score(r["t"], rows) >= 2 and not any(abs(c["t"] - r["t"]) < 15 for c in cands):
            cands.append({"t": int(r["t"]) + 3, "score": cue_score(r["t"], rows), "diff": 0.0})
    cands.sort(key=lambda c: (c["score"], c["diff"]), reverse=True)
    chosen = []
    for c in cands:
        if all(abs(c["t"] - o["t"]) >= 12 for o in chosen):
            chosen.append(c)
        if len(chosen) >= MAX_SHOTS:
            break
    return sorted(chosen, key=lambda c: c["t"])


def grab_frame(video, t, out):
    cmd = [ffmpeg_exe(), "-v", "error", "-threads", "1", "-y", "-ss", str(t), "-i", str(video),
           "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3", str(out)]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    return Path(out).exists() and Path(out).stat().st_size > 2000


# ---------- phase 3: AI check ----------
def vision_check(jpg_path, context):
    """Ask Claude whether the frame is a chart/graph/table/diagram. Returns dict or None on failure."""
    try:
        import anthropic
        data = base64.standard_b64encode(Path(jpg_path).read_bytes()).decode()
        msg = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                {"type": "text", "text": (
                    "This is a frame from a video. The speaker was saying around this moment: "
                    f"\"{context[:600]}\"\n\n"
                    "Is the frame showing a chart, graph, table, data slide or diagram that helps explain "
                    "what is being said? A speaker's face, a title card or a plain photo is NOT one. "
                    "Reply with JSON only: {\"is_chart\": true/false, \"kind\": \"bar chart / line graph / "
                    "table / diagram / slide\", \"caption\": \"one plain sentence saying what it shows\"}")}]}])
        m = re.search(r"\{.*\}", "".join(b.text for b in msg.content if getattr(b, "text", None)), re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


# ---------- the whole run ----------
def _download(vid, folder):
    import yt_dlp
    opts = {"format": "best[height<=720][ext=mp4]/best[height<=720]/best", "quiet": True, "noplaylist": True,
            "outtmpl": str(Path(folder) / "v.%(ext)s"), "max_filesize": 1_500_000_000}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(youtube.watch_url(vid), download=True)
        return y.prepare_filename(info)


def process_video(item_id, vid, upload_path=None):
    set_state(item_id, "queued", "")
    with _gate:
        tmp = tempfile.mkdtemp(prefix="vid_")
        try:
            _run(item_id, vid, upload_path, tmp)
        except Exception as e:
            set_state(item_id, "error", f"Something went wrong: {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            if upload_path:
                try:
                    Path(upload_path).unlink()
                except OSError:
                    pass


def _run(item_id, vid, upload_path, tmp):
    notes = []
    # phase 1
    set_state(item_id, "transcript")
    rows, why = fetch_transcript(vid)
    if rows:
        set_state(item_id, "transcript", transcript=json.dumps(rows))
    else:
        notes.append(why)
    # the video file
    video = None
    if upload_path and Path(upload_path).exists():
        video = upload_path
    elif ALLOW_DOWNLOAD:
        set_state(item_id, "frames", "")
        try:
            video = _download(vid, tmp)
        except Exception as e:
            notes.append(f"Could not download the video ({type(e).__name__}).")
    else:
        notes.append("No screenshots: the video file is needed. Upload it with the link, or ask the "
                     "administrator to enable YouTube downloads.")
    shots = []
    if video:
        try:
            ffmpeg_exe()
        except Exception:
            notes.append("No screenshots: ffmpeg is missing (pip install imageio-ffmpeg).")
            video = None
    if video:
        # phase 2
        set_state(item_id, "frames")
        diffs = sample_diffs(video)
        moments = pick_moments(diffs, rows or []) if diffs else []
        use_ai = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if not use_ai:
            notes.append("Screenshots are not AI-checked (no ANTHROPIC_API_KEY), so a few may not be graphs.")
        for n, m in enumerate(moments):
            out = Path(tmp) / f"f{n}.jpg"
            if not grab_frame(video, m["t"], out):
                continue
            info = {"is_chart": True, "kind": "slide or graph", "caption": ""}
            if use_ai:  # phase 3
                set_state(item_id, "vision")
                ctx = " ".join(r["text"] for r in (rows or []) if m["t"] - 10 <= r["t"] <= m["t"] + 20)
                res = vision_check(out, ctx)
                if res is not None:
                    info = res
            if not info.get("is_chart"):
                continue
            data = out.read_bytes()
            name = hashlib.sha1(data).hexdigest()[:20] + ".jpg"
            media.MEDIA_DIR.mkdir(exist_ok=True)
            (media.MEDIA_DIR / name).write_bytes(data)
            shots.append({"t": m["t"], "file": name, "kind": str(info.get("kind", ""))[:40],
                          "caption": str(info.get("caption", ""))[:300]})
        if not shots:
            notes.append("No graphs or slides were found in this video.")
    set_state(item_id, "done", " ".join(n for n in notes if n), shots=json.dumps(shots) if shots else "")
