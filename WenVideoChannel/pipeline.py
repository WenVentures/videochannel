#!/usr/bin/env python3
"""
Automated social-video pipeline.

Project dir (auto-created on first run):
    ~/src/WenVideoChannel/
        pipeline.py     — this script
        scripts.json    — bank of scripts (rotated through)
        youtube_auth.py — one-time YouTube OAuth helper
        state.json      — tracks used ids + post history
        .env            — API keys (PEXELS_KEY, IG_TOKEN, IG_BUSINESS_ID,
                          CLOUDINARY_CLOUD_NAME, CLOUDINARY_UPLOAD_PRESET,
                          YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN)
        runs/<UTC-timestamp>/
            raw/*.mp4   — Pexels downloads + intermediate files
            final.mp4   — 1080x1920 vertical reel, captions burned in, voiceover
            caption.txt — caption text for IG / TikTok / YouTube
            post.log    — run log

Modes:
    python3 pipeline.py --dry-run        # build, open, don't post
    python3 pipeline.py                  # build + post (requires full .env)
    python3 pipeline.py --script-id XYZ  # force a specific script
    python3 pipeline.py --list           # list scripts in bank
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR = Path.home() / "src" / "WenVideoChannel"
SCRIPTS_FILE = CONFIG_DIR / "scripts.json"
STATE_FILE = CONFIG_DIR / "state.json"
ENV_FILE = CONFIG_DIR / ".env"
PROJECTS_DIR = CONFIG_DIR / "runs"

VOICE_NAME = "Zoe (Premium)"
VOICE_RATE = 170
W, H, FPS = 1080, 1920, 30
CLIP_SECONDS = 5
TOTAL_SECONDS = 30


# ---------------------------------------------------------------- env / state

def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_scripts() -> list:
    if not SCRIPTS_FILE.exists():
        sys.exit(f"Script bank not found at {SCRIPTS_FILE}. Copy scripts.json there.")
    return json.loads(SCRIPTS_FILE.read_text())


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"used_ids": [], "post_history": []}


def save_state(state: dict) -> None:
    CONFIG_DIR.mkdir(exist_ok=True, parents=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def pick_script(scripts: list, state: dict, force_id: str | None = None) -> dict:
    if force_id:
        match = next((s for s in scripts if s["id"] == force_id), None)
        if not match:
            sys.exit(f"No script with id '{force_id}'")
        return match
    used = set(state.get("used_ids", []))
    unused = [s for s in scripts if s["id"] not in used]
    if not unused:
        state["used_ids"] = []
        unused = list(scripts)
    return random.choice(unused)


# --------------------------------------------------------------- io helpers

def log(run_dir: Path, msg: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with (run_dir / "post.log").open("a") as f:
        f.write(line + "\n")


def run_cmd(cmd: list) -> None:
    subprocess.run(cmd, check=True)


def require_bin(name: str) -> None:
    if not shutil.which(name):
        sys.exit(f"Required binary '{name}' not found on PATH.")


# ----------------------------------------------------------------- Pexels

def search_pexels(query: str, api_key: str, count: int = 6, orientation: str = "portrait") -> list:
    params = urllib.parse.urlencode({
        "query": query,
        "per_page": max(count * 3, 15),
        "orientation": orientation,
        "size": "medium",
    })
    url = f"https://api.pexels.com/videos/search?{params}"
    req = urllib.request.Request(url, headers={
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    picks = []
    for v in data.get("videos", []):
        if (v.get("duration") or 0) < 6:
            continue  # need 5s usable + 2s head trim
        files = v.get("video_files", [])
        portrait_files = [f for f in files
                          if (f.get("height") or 0) >= (f.get("width") or 0)
                          and (f.get("height") or 0) >= 720]
        if not portrait_files:
            continue
        portrait_files.sort(key=lambda f: abs((f.get("height") or 0) - 1920))
        picks.append({"id": v["id"], "url": portrait_files[0]["link"]})
        if len(picks) >= count:
            break
    return picks


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


# ------------------------------------------------------------ video build

def ass_time(t: float) -> str:
    """ASS timestamps use h:mm:ss.cs (centiseconds)."""
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def caption_chunks(text: str) -> list[str]:
    """Break the voiceover into 3–5 word caption chunks, skipping punctuation-only runs."""
    cleaned = text.replace("—", "-").replace("…", "...").strip()
    phrases = re.split(r'(?<=[.!?])\s+|\n+', cleaned)
    chunks: list[str] = []
    for p in phrases:
        words = [w for w in p.split() if re.search(r'[A-Za-z0-9]', w)]
        while words:
            take = min(5, len(words))
            chunks.append(" ".join(words[:take]))
            words = words[take:]
    return chunks


# Font for burned-in captions. Helvetica.ttc ships with every macOS; if it's
# ever missing we fall back to Arial.
CAPTION_FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
]


def find_caption_font() -> str:
    for p in CAPTION_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise RuntimeError(
        "No usable system font found. Install Helvetica or Arial, or edit "
        "CAPTION_FONT_CANDIDATES in pipeline.py to point at a .ttf/.ttc file."
    )


def write_caption_textfiles(text: str, run_dir: Path,
                             total_duration: float) -> list[tuple[str, float, float]]:
    """Split the voiceover into caption chunks, write each to cap_N.txt,
    and return [(basename, start_sec, end_sec), ...]."""
    chunks = caption_chunks(text)
    if not chunks:
        return []
    per = total_duration / len(chunks)
    out = []
    for i, c in enumerate(chunks):
        name = f"cap_{i}.txt"
        (run_dir / name).write_text(c)
        out.append((name, i * per, (i + 1) * per))
    return out


def build_drawtext_vf(chunks_meta: list[tuple[str, float, float]],
                      font_path: str) -> str:
    """Build a -vf value that stacks one drawtext per caption chunk.
    Uses textfile= so we never have to escape caption text in the filter string."""
    if not chunks_meta:
        return "null"
    parts = []
    for name, start, end in chunks_meta:
        parts.append(
            f"drawtext=fontfile='{font_path}'"
            f":textfile={name}"
            f":fontsize=64"
            f":fontcolor=white"
            f":borderw=5"
            f":bordercolor=black"
            f":x=(w-text_w)/2"
            f":y=(h-text_h)/2"
            # commas inside the enable expression must be escaped so the
            # filter-graph parser doesn't treat them as filter separators.
            f":enable='between(t\\,{start:.3f}\\,{end:.3f})'"
        )
    return ",".join(parts)


def build_video(clip_paths: list[Path], voiceover_text: str, final_path: Path, run_dir: Path) -> None:
    raw = run_dir / "raw"; raw.mkdir(exist_ok=True)

    norms = []
    for i, p in enumerate(clip_paths, 1):
        trim = raw / f"trim{i}.mp4"
        norm = raw / f"norm{i}.mp4"
        run_cmd(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", "00:00:02", "-i", str(p), "-t", str(CLIP_SECONDS),
                 "-c:v", "libx264", "-c:a", "aac", str(trim)])
        run_cmd(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(trim),
                 "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                        f"crop={W}:{H},setsar=1",
                 "-r", str(FPS),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                 str(norm)])
        norms.append(norm)

    filelist = raw / "filelist.txt"
    filelist.write_text("\n".join(f"file '{n}'" for n in norms))

    concat = run_dir / "concat.mp4"
    run_cmd(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", str(filelist),
             "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-c:a", "aac",
             str(concat)])

    voice = run_dir / "voice.aiff"
    run_cmd(["say", "-v", VOICE_NAME, "-r", str(VOICE_RATE), "-o", str(voice), voiceover_text])

    voice_dur = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(voice)]).strip())

    # Burn timed captions with drawtext (needs only FreeType — no libass).
    # We run ffmpeg with cwd=run_dir so the cap_N.txt textfiles resolve by
    # basename, which keeps slashes out of the filter string.
    font_path = find_caption_font()
    chunks_meta = write_caption_textfiles(
        voiceover_text, run_dir, min(voice_dur, TOTAL_SECONDS)
    )
    vf = build_drawtext_vf(chunks_meta, font_path)

    # Pass 1: burn captions into the concatenated clip (video only).
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", "concat.mp4",
         "-vf", vf,
         "-c:v", "libx264", "-crf", "23", "-preset", "medium",
         "-an",
         "subbed.mp4"],
        check=True, cwd=str(run_dir),
    )
    subbed = run_dir / "subbed.mp4"

    # Pass 2: mux the voiceover audio onto the captioned video and pad
    # silence to the target length.
    run_cmd(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(subbed), "-i", str(voice),
             "-filter_complex", f"[1:a]apad=pad_dur={TOTAL_SECONDS}[a]",
             "-map", "0:v:0", "-map", "[a]",
             "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k",
             "-t", str(TOTAL_SECONDS), "-movflags", "+faststart",
             str(final_path)])


# -------------------------------------------------------------- posting

def yt_refresh_access_token(env: dict) -> str:
    """Trade the stored refresh token for a 1-hour access token."""
    data = urllib.parse.urlencode({
        "client_id": env["YT_CLIENT_ID"],
        "client_secret": env["YT_CLIENT_SECRET"],
        "refresh_token": env["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())
    if "access_token" not in payload:
        raise RuntimeError(f"YouTube token refresh failed: {payload}")
    return payload["access_token"]


def make_yt_title(script: dict) -> str:
    first_line = (script.get("caption") or "").split("\n")[0]
    first_line = re.sub(r"#\S+", "", first_line).strip(" -—|")
    if not first_line:
        first_line = script.get("voiceover", "").split(".")[0].strip()
    # YouTube titles cap at 100 chars; keep some room for the Shorts tag
    if len(first_line) > 80:
        first_line = first_line[:80].rsplit(" ", 1)[0]
    return f"{first_line} #Shorts"


def make_yt_tags(script: dict) -> list[str]:
    tags = re.findall(r"#(\w+)", script.get("caption", ""))
    if script.get("topic"):
        tags.append(script["topic"])
    tags.append("shorts")
    # de-dupe, preserve order, YT caps total tag text at 500 chars
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out[:15]


def post_to_youtube(video_path: Path, script: dict, env: dict, run_dir: Path) -> dict:
    access = yt_refresh_access_token(env)
    privacy = env.get("YT_PRIVACY", "public").strip() or "public"

    metadata = {
        "snippet": {
            "title": make_yt_title(script),
            "description": script.get("caption", ""),
            "categoryId": "22",  # People & Blogs
            "tags": make_yt_tags(script),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    # 1) Start a resumable session to get the upload URL.
    size = video_path.stat().st_size
    body = json.dumps(metadata).encode()
    start_url = (
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status"
    )
    req = urllib.request.Request(start_url, data=body, method="POST", headers={
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(size),
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        upload_url = r.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube: no resumable upload URL returned")

    # 2) PUT the video bytes to that URL. curl handles the streaming better
    #    than urllib for a file of this size.
    result = subprocess.run(
        ["curl", "-sS", "-X", "PUT",
         "-H", "Content-Type: video/mp4",
         "--data-binary", f"@{video_path}",
         upload_url],
        capture_output=True, check=True, text=True,
    )
    resp = json.loads(result.stdout)
    if "id" not in resp:
        raise RuntimeError(f"YouTube upload failed: {resp}")
    return resp


def upload_to_cloudinary(video_path: Path, env: dict) -> str:
    cloud = env["CLOUDINARY_CLOUD_NAME"]
    preset = env["CLOUDINARY_UPLOAD_PRESET"]
    url = f"https://api.cloudinary.com/v1_1/{cloud}/video/upload"
    out = subprocess.run(
        ["curl", "-sS", "-X", "POST",
         "-F", f"file=@{video_path}",
         "-F", f"upload_preset={preset}",
         url],
        capture_output=True, check=True, text=True,
    )
    data = json.loads(out.stdout)
    if "secure_url" not in data:
        raise RuntimeError(f"Cloudinary upload failed: {data}")
    return data["secure_url"]


def post_to_instagram_reel(video_url: str, caption: str, env: dict, run_dir: Path) -> dict:
    tok = env["IG_TOKEN"]
    ig_id = env["IG_BUSINESS_ID"]
    base = "https://graph.facebook.com/v20.0"

    create_params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": tok,
    }
    r = subprocess.run(
        ["curl", "-sS", "-X", "POST",
         f"{base}/{ig_id}/media?{urllib.parse.urlencode(create_params)}"],
        capture_output=True, check=True, text=True,
    )
    created = json.loads(r.stdout)
    if "id" not in created:
        raise RuntimeError(f"IG create-container failed: {created}")
    container_id = created["id"]
    log(run_dir, f"  container id: {container_id}; waiting for processing…")

    for _ in range(60):
        time.sleep(5)
        status = subprocess.run(
            ["curl", "-sS",
             f"{base}/{container_id}?fields=status_code&access_token={tok}"],
            capture_output=True, check=True, text=True,
        )
        sd = json.loads(status.stdout)
        code = sd.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"IG container error: {sd}")
    else:
        raise RuntimeError("IG container never finished processing (5 min timeout)")

    pub_params = {"creation_id": container_id, "access_token": tok}
    r = subprocess.run(
        ["curl", "-sS", "-X", "POST",
         f"{base}/{ig_id}/media_publish?{urllib.parse.urlencode(pub_params)}"],
        capture_output=True, check=True, text=True,
    )
    return json.loads(r.stdout)


# ------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Build video, open, skip posting")
    ap.add_argument("--script-id", help="Force a specific script id")
    ap.add_argument("--list", action="store_true", help="List scripts in bank and exit")
    args = ap.parse_args()

    for b in ("ffmpeg", "ffprobe", "say", "curl"):
        require_bin(b)

    scripts = load_scripts()
    state = load_state()

    if args.list:
        used = set(state.get("used_ids", []))
        for s in scripts:
            mark = "✓" if s["id"] in used else " "
            print(f"[{mark}] {s['id']:30s}  topic={s['topic']:15s} hook={s.get('hook_style','?')}")
        return

    env = load_env()
    if "PEXELS_KEY" not in env:
        sys.exit("PEXELS_KEY missing from ~/src/WenVideoChannel/.env")

    chosen = pick_script(scripts, state, args.script_id)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    run_dir = PROJECTS_DIR / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    log(run_dir, f"Script: id={chosen['id']} topic={chosen['topic']} hook={chosen.get('hook_style','?')}")
    log(run_dir, f"Query: {chosen['pexels_query']}")

    hits = search_pexels(chosen["pexels_query"], env["PEXELS_KEY"], count=6)
    log(run_dir, f"Pexels returned {len(hits)} usable portrait clips")
    if len(hits) < 6:
        sys.exit(f"Not enough clips for query '{chosen['pexels_query']}'. "
                 f"Edit the query in scripts.json or pick another script.")

    raw_dir = run_dir / "raw"; raw_dir.mkdir(exist_ok=True)
    clip_paths = []
    for i, v in enumerate(hits, 1):
        p = raw_dir / f"clip{i}.mp4"
        download(v["url"], p)
        clip_paths.append(p)
    log(run_dir, "Clips downloaded")

    final = run_dir / "final.mp4"
    build_video(clip_paths, chosen["voiceover"], final, run_dir)
    log(run_dir, f"Built {final}")

    caption_path = run_dir / "caption.txt"
    caption_path.write_text(chosen["caption"])

    if args.dry_run:
        log(run_dir, "Dry run — skipping posts")
        subprocess.run(["open", str(final)])
        return

    posted_any = False

    have_yt = all(k in env for k in
                  ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))
    if have_yt:
        try:
            log(run_dir, "Uploading to YouTube…")
            yresp = post_to_youtube(final, chosen, env, run_dir)
            log(run_dir, f"  YT video id: {yresp.get('id')}  status: {yresp.get('status', {}).get('privacyStatus')}")
            posted_any = True
        except Exception as e:
            log(run_dir, f"YouTube upload failed: {e}")
    else:
        log(run_dir, "YouTube creds missing — skipped YouTube.")

    have_ig = all(k in env for k in
                  ("IG_TOKEN", "IG_BUSINESS_ID",
                   "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_UPLOAD_PRESET"))
    if have_ig:
        try:
            log(run_dir, "Uploading to Cloudinary…")
            url = upload_to_cloudinary(final, env)
            log(run_dir, f"  uploaded: {url}")
            log(run_dir, "Posting to Instagram…")
            resp = post_to_instagram_reel(url, chosen["caption"], env, run_dir)
            log(run_dir, f"IG response: {resp}")
            posted_any = True
        except Exception as e:
            log(run_dir, f"Instagram upload failed: {e}")
    else:
        log(run_dir, "IG / Cloudinary creds missing — skipped Instagram.")

    if not posted_any:
        log(run_dir, "Nothing posted. Run with --dry-run to QA, or fill in .env.")

    state.setdefault("used_ids", []).append(chosen["id"])
    state.setdefault("post_history", []).append({
        "id": chosen["id"],
        "topic": chosen["topic"],
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "file": str(final),
    })
    save_state(state)
    log(run_dir, "Done.")


if __name__ == "__main__":
    main()
