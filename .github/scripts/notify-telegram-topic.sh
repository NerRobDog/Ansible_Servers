#!/usr/bin/env bash
set -euo pipefail

message="${1:-}"

if [[ -z "${message}" ]]; then
  echo "Usage: notify-telegram-topic.sh '<message>'" >&2
  exit 2
fi

if [[ -z "${ALERT_TELEGRAM_BOT_TOKEN:-}" || -z "${ALERT_TELEGRAM_CHAT_ID:-}" ]]; then
  echo "Telegram secrets are not configured. Skip notification." >&2
  exit 0
fi

api_url="https://api.telegram.org/bot${ALERT_TELEGRAM_BOT_TOKEN}/sendMessage"

curl_args=(
  --silent
  --show-error
  --fail
  --request POST
  --data-urlencode "chat_id=${ALERT_TELEGRAM_CHAT_ID}"
  --data-urlencode "text=${message}"
  --data-urlencode "disable_web_page_preview=true"
)

if [[ -n "${ALERT_TELEGRAM_TOPIC_ID:-}" ]]; then
  curl_args+=(--data-urlencode "message_thread_id=${ALERT_TELEGRAM_TOPIC_ID}")
fi

curl "${curl_args[@]}" "${api_url}" >/dev/null
