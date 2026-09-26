#!/usr/bin/env bash
# build_city_video.sh
# Compiles a 30-second "city timelapse" video from 6 Pexels clips and
# overlays a scripted Samantha voiceover — a reflection on city love,
# shaped for Instagram engagement.
#
# Usage:  bash build_city_video.sh
# Output: ~/Desktop/video_project/city/output/final_30sec.mp4

set -euo pipefail

# --------------------------------------------------------------------
# Config
# --------------------------------------------------------------------
TOPIC="city"
ROOT="$HOME/Desktop/video_project/$TOPIC"
RAW="$ROOT/raw"
OUT="$ROOT/output"
VOICE_NAME="Samantha"
VOICE_RATE=170                  # words per minute
CLIP_SECONDS=5
TOTAL_SECONDS=30
WIDTH=1920
HEIGHT=1080
FPS=30

# Six Pexels city-timelapse video IDs, verified via Pexels search.
# If one ever 404s, swap it for another ID from
# https://www.pexels.com/search/videos/city%20timelapse/
PEXELS_IDS=(
  1654216    # View of city in timelapse mode — Amit
  5595352    # Time-lapse of a city during nighttime — taro
  5625355    # Timelapse video of a city at night
  5544054    # Time-lapse of a city intersection — Timo Volz
  12659228   # Time lapse of a futuristic city — Timo Volz
  5292160    # Time-lapse footage of a city sky
)

# The voiceover script. ~85 words → ~30 seconds at 170 wpm.
read -r -d '' VOICEOVER_TEXT <<'EOF' || true
Have you ever fallen in love with a city? Not a person in it — the city itself.
The blur of traffic lights. The hum of streets that never sleep.
The stranger at the coffee shop who becomes your Tuesday ritual.
A city doesn't ask who you are. It only asks that you show up.
Every skyline, every crowded corner, every rain-slick sidewalk whispers the same thing — you are part of something endless.
Fall in love with your city. It has been waiting for you.
EOF

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m!! %s\033[0m\n' "$*" >&2; exit 1; }

require_cmd() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    if [ "$name" = "ffmpeg" ] && command -v brew >/dev/null 2>&1; then
      log "ffmpeg not found — installing via Homebrew…"
      brew install ffmpeg
    else
      die "'$name' is required but not installed. Install it (e.g. 'brew install $name') and re-run."
    fi
  fi
}

# --------------------------------------------------------------------
# Step 1 — dependencies
# --------------------------------------------------------------------
log "Step 1 — checking dependencies"
require_cmd curl
require_cmd ffmpeg
require_cmd ffprobe
require_cmd say      # macOS built-in TTS (for the Samantha voiceover)

# --------------------------------------------------------------------
# Step 2 — folders
# --------------------------------------------------------------------
log "Step 2 — creating working folders under $ROOT"
mkdir -p "$RAW" "$OUT"

# --------------------------------------------------------------------
# Step 3 — download 6 Pexels clips
# --------------------------------------------------------------------
log "Step 3 — downloading 6 Pexels clips"

download_pexels() {
  local id="$1"
  local dest="$2"
  # Pexels' canonical free-download URL redirects to the MP4. A realistic
  # User-Agent helps it serve the 302 rather than bouncing us to the page.
  local url="https://www.pexels.com/download/video/${id}/"
  curl -fL --retry 3 --retry-delay 2 \
       -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36" \
       -o "$dest" "$url"
}

i=1
for id in "${PEXELS_IDS[@]}"; do
  dest="$RAW/clip${i}.mp4"
  if [ -s "$dest" ]; then
    log "  clip${i}.mp4 already exists — skipping"
  else
    log "  downloading clip${i}.mp4 (Pexels id ${id})"
    if ! download_pexels "$id" "$dest"; then
      die "Failed to download Pexels id ${id}. Swap it in PEXELS_IDS for another id from pexels.com/search/videos/city%20timelapse/, then re-run."
    fi
    # sanity check — a real clip is at least a few hundred KB
    bytes=$(wc -c <"$dest" | tr -d ' ')
    if [ "$bytes" -lt 200000 ]; then
      die "Downloaded clip${i}.mp4 is only ${bytes} bytes — the URL probably served an HTML page instead of the video. Check Pexels id ${id} manually."
    fi
  fi
  i=$((i+1))
done

# --------------------------------------------------------------------
# Step 4 — trim each clip to exactly CLIP_SECONDS
# --------------------------------------------------------------------
log "Step 4 — trimming each clip to ${CLIP_SECONDS}s"
for i in 1 2 3 4 5 6; do
  ffmpeg -hide_banner -loglevel error -y \
    -ss 00:00:02 -i "$RAW/clip${i}.mp4" \
    -t "$CLIP_SECONDS" \
    -c:v libx264 -c:a aac \
    "$RAW/trim${i}.mp4"
done

# --------------------------------------------------------------------
# Step 5 — normalise all clips to the same resolution/framerate
# --------------------------------------------------------------------
log "Step 5 — normalising clips to ${WIDTH}x${HEIGHT} @ ${FPS}fps"
for i in 1 2 3 4 5 6; do
  ffmpeg -hide_banner -loglevel error -y \
    -i "$RAW/trim${i}.mp4" \
    -vf "scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1" \
    -r "$FPS" \
    -c:v libx264 -pix_fmt yuv420p -c:a aac \
    "$RAW/norm${i}.mp4"
done

# --------------------------------------------------------------------
# Step 6 — build a concat file list
# --------------------------------------------------------------------
log "Step 6 — writing concat file list"
FILELIST="$ROOT/filelist.txt"
: > "$FILELIST"
for i in 1 2 3 4 5 6; do
  printf "file '%s/norm%d.mp4'\n" "$RAW" "$i" >>"$FILELIST"
done

# --------------------------------------------------------------------
# Step 7 — concatenate into a single 30-second video
# --------------------------------------------------------------------
log "Step 7 — concatenating into a 30s video"
CONCAT="$OUT/concat_30sec.mp4"
ffmpeg -hide_banner -loglevel error -y \
  -f concat -safe 0 -i "$FILELIST" \
  -c:v libx264 -crf 23 -preset medium -c:a aac \
  "$CONCAT"

# --------------------------------------------------------------------
# Step 8 — generate the Samantha voiceover
# --------------------------------------------------------------------
log "Step 8 — generating the ${VOICE_NAME} voiceover"
VOICE_AIFF="$ROOT/voice.aiff"
say -v "$VOICE_NAME" -r "$VOICE_RATE" -o "$VOICE_AIFF" "$VOICEOVER_TEXT"

# Report how long the voiceover actually is, so we know if we need to
# tweak VOICE_RATE later.
VOICE_DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VOICE_AIFF" | awk '{printf "%.2f", $1}')
log "  voiceover duration: ${VOICE_DUR}s (target: ${TOTAL_SECONDS}s)"

# --------------------------------------------------------------------
# Step 9 — mix voiceover onto the concatenated video
# --------------------------------------------------------------------
# We replace the original clip audio with the voiceover and pad with
# silence so the final file is exactly TOTAL_SECONDS long.
log "Step 9 — mixing voiceover onto the video"
FINAL="$OUT/final_30sec.mp4"
ffmpeg -hide_banner -loglevel error -y \
  -i "$CONCAT" -i "$VOICE_AIFF" \
  -filter_complex "[1:a]apad=pad_dur=${TOTAL_SECONDS}[aout]" \
  -map 0:v:0 -map "[aout]" \
  -c:v copy -c:a aac -b:a 192k \
  -t "$TOTAL_SECONDS" -movflags +faststart \
  "$FINAL"

# --------------------------------------------------------------------
# Step 10 — verify and open
# --------------------------------------------------------------------
log "Step 10 — verifying output"
FINAL_DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$FINAL" | awk '{printf "%.2f", $1}')
log "  final_30sec.mp4 duration: ${FINAL_DUR}s"

log "Done. Opening $FINAL"
open "$FINAL"
