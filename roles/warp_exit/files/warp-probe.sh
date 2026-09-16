#!/usr/bin/env bash
# Sends real requests through the WARP interface and publishes the result for
# node-exporter's textfile collector. A fresh handshake proves nothing on its own:
# a degraded free registration keeps handshaking while most tunneled requests hang.
set -uo pipefail

iface="${WARP_PROBE_IFACE:-warp}"
out="${WARP_PROBE_OUT:-/var/lib/node_exporter/textfile/warp.prom}"
curl_bin="${WARP_PROBE_CURL:-curl}"
wg_bin="${WARP_PROBE_WG:-wg}"
trace_url="${WARP_PROBE_TRACE_URL:-https://www.cloudflare.com/cdn-cgi/trace}"
g204_url="${WARP_PROBE_204_URL:-https://www.gstatic.com/generate_204}"
per_target="${WARP_PROBE_PER_TARGET:-5}"
timeout_s="${WARP_PROBE_TIMEOUT:-5}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

probe() {
  local kind="$1" url="$2" n="$3" meta
  meta="$("$curl_bin" --interface "$iface" --silent --max-time "$timeout_s" \
    --output "$work/$kind.$n.body" --write-out '%{http_code} %{time_total}' "$url" 2>/dev/null)" || meta="000 0"
  printf '%s\n' "$meta" > "$work/$kind.$n.meta"
}

for n in $(seq 1 "$per_target"); do
  probe trace "$trace_url" "$n" &
  probe g204 "$g204_url" "$n" &
done
wait

total=0
ok=0
warp_on=0
latency_sum=0
for meta in "$work"/*.meta; do
  total=$((total + 1))
  read -r code seconds < "$meta"
  kind="$(basename "$meta")"
  kind="${kind%%.*}"
  success=0
  if [[ "$kind" == trace && "$code" == 200 ]] && grep -qx 'warp=on' "${meta%.meta}.body" 2>/dev/null; then
    success=1
    warp_on=1
  elif [[ "$kind" == g204 && "$code" == 204 ]]; then
    success=1
  fi
  if ((success)); then
    ok=$((ok + 1))
    latency_sum="$(awk -v a="$latency_sum" -v b="$seconds" 'BEGIN { printf "%.6f", a + b }')"
  fi
done

ratio="$(awk -v o="$ok" -v t="$total" 'BEGIN { if (t == 0) print "0"; else printf "%.6f", o / t }')"
if ((ok > 0)); then
  latency="$(awk -v s="$latency_sum" -v o="$ok" 'BEGIN { printf "%.6f", s / o }')"
else
  latency="NaN"
fi

now="${WARP_PROBE_NOW:-$(date +%s)}"
handshake="$("$wg_bin" show "$iface" latest-handshakes 2>/dev/null | awk 'NR == 1 { print $2 }')"
if [[ "$handshake" =~ ^[0-9]+$ ]] && ((handshake > 0)); then
  handshake_age=$((now - handshake))
else
  handshake_age="NaN"
fi

# Write next to the target and rename: node-exporter must never read a half-written
# file, and it only collects *.prom, so the temporary name is ignored.
tmp="$(mktemp "${out}.XXXXXX")"
{
  echo "# HELP warp_probe_success_ratio Share of probe requests through the WARP interface that succeeded in the last run."
  echo "# TYPE warp_probe_success_ratio gauge"
  echo "warp_probe_success_ratio $ratio"
  echo "# HELP warp_probe_latency_avg_seconds Mean duration of successful probe requests in the last run."
  echo "# TYPE warp_probe_latency_avg_seconds gauge"
  echo "warp_probe_latency_avg_seconds $latency"
  echo "# HELP warp_status_on Whether Cloudflare reported warp=on for traffic from this interface."
  echo "# TYPE warp_status_on gauge"
  echo "warp_status_on $warp_on"
  echo "# HELP warp_handshake_age_seconds Seconds since the last WireGuard handshake with the WARP peer."
  echo "# TYPE warp_handshake_age_seconds gauge"
  echo "warp_handshake_age_seconds $handshake_age"
  echo "# HELP warp_probe_last_run_timestamp_seconds Unix time the probe last finished."
  echo "# TYPE warp_probe_last_run_timestamp_seconds gauge"
  echo "warp_probe_last_run_timestamp_seconds $now"
} > "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$out"
