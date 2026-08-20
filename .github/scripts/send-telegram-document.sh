#!/usr/bin/env bash
# Send one or more files to the Telegram alert topic via sendDocument.
#
# Companion to notify-telegram-topic.sh, which only sends text. Kept separate so
# the text path stays dependency-free and callers cannot accidentally ship a
# large upload where a one-line alert was intended.
set -euo pipefail

caption=""
declare -a files=()

usage() {
  cat <<'EOF'
Usage: send-telegram-document.sh [--caption <text>] <file> [<file> ...]

Requires ALERT_TELEGRAM_BOT_TOKEN and ALERT_TELEGRAM_CHAT_ID in the environment.
ALERT_TELEGRAM_TOPIC_ID is optional (forum topic thread id).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --caption) caption="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) files+=("$1"); shift ;;
  esac
done

if [[ ${#files[@]} -eq 0 ]]; then
  usage
  exit 2
fi

if [[ -z "${ALERT_TELEGRAM_BOT_TOKEN:-}" || -z "${ALERT_TELEGRAM_CHAT_ID:-}" ]]; then
  echo "Telegram secrets are not configured. Skip document upload." >&2
  exit 0
fi

api_url="https://api.telegram.org/bot${ALERT_TELEGRAM_BOT_TOKEN}/sendDocument"

# Bot API hard limit for sendDocument.
max_bytes=$((50 * 1024 * 1024))

total=${#files[@]}
index=0
failures=0

for f in "${files[@]}"; do
  index=$((index + 1))
  if [[ ! -f "$f" ]]; then
    echo "Skip missing file: $f" >&2
    continue
  fi

  size=$(wc -c < "$f" | tr -d ' ')
  if (( size > max_bytes )); then
    echo "Skip ${f}: ${size} bytes exceeds the 50MB sendDocument limit." >&2
    failures=$((failures + 1))
    continue
  fi

  this_caption="$caption"
  if (( total > 1 )); then
    this_caption="${caption}
part ${index}/${total}: $(basename "$f")"
  fi
  # Telegram truncates captions past 1024 chars and rejects some overlong ones.
  this_caption="${this_caption:0:1000}"

  curl_args=(
    --silent --show-error --fail
    --request POST
    --form "chat_id=${ALERT_TELEGRAM_CHAT_ID}"
    --form "document=@${f}"
    --form "caption=${this_caption}"
  )
  if [[ -n "${ALERT_TELEGRAM_TOPIC_ID:-}" ]]; then
    curl_args+=(--form "message_thread_id=${ALERT_TELEGRAM_TOPIC_ID}")
  fi

  if curl "${curl_args[@]}" "$api_url" >/dev/null; then
    echo "Sent ${f} (${size} bytes)"
  else
    echo "Failed to send ${f}" >&2
    failures=$((failures + 1))
  fi
done

if (( failures > 0 )); then
  echo "${failures} file(s) failed to send." >&2
  exit 1
fi
