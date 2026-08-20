#!/usr/bin/env bash
# Collect a day of logs from every fleet host and emit one archive.
#
# Logs are streamed straight off each host (journalctl | gzip over ssh) and land
# only on the runner — nothing is written to the servers' disks. That matters:
# dh-germ-1 filled its 9.8G disk to 100% in 2026-08 partly through log growth,
# so a collector that staged temp files server-side would recreate the problem
# it exists to observe.
#
# Per-host failures are recorded and skipped, never fatal. The smoke script used
# to abort the whole fleet loop on the first bad host, which silently left every
# host after it unchecked for days; do not repeat that here.
set -uo pipefail

inventory=""
out_archive=""
since="24 hours ago"
limit="all"

usage() {
  cat <<'EOF'
Usage: collect-fleet-logs.sh --inventory <hosts.ini> --out <archive.tar> [--since <spec>] [--limit <all|host1,host2>]

Streams journald + docker container logs from each fleet host into one tar archive.
Writes a MANIFEST.txt describing what was collected and what failed.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inventory) inventory="${2:-}"; shift 2 ;;
    --out) out_archive="${2:-}"; shift 2 ;;
    --since) since="${2:-}"; shift 2 ;;
    --limit) limit="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$inventory" || -z "$out_archive" ]]; then
  usage
  exit 2
fi
if [[ ! -f "$inventory" ]]; then
  echo "Inventory not found: $inventory" >&2
  exit 1
fi

# Parse "alias ansible_host=IP ansible_user=U ansible_port=P" lines out of the
# rendered inventory. Group headers ([foo]) and blanks are skipped.
declare -a aliases=() hosts=() users=() ports=()
while IFS= read -r line; do
  [[ "$line" =~ ^\[ ]] && continue
  [[ -z "${line// }" ]] && continue
  alias_name="${line%% *}"
  [[ -z "$alias_name" ]] && continue
  host=""; user="root"; port="22"
  for tok in $line; do
    case "$tok" in
      ansible_host=*) host="${tok#ansible_host=}" ;;
      ansible_user=*) user="${tok#ansible_user=}" ;;
      ansible_port=*) port="${tok#ansible_port=}" ;;
    esac
  done
  [[ -z "$host" ]] && continue
  # Inventory lists a host once per group; keep the first occurrence only.
  already=0
  for seen in "${aliases[@]:-}"; do
    [[ "$seen" == "$alias_name" ]] && already=1 && break
  done
  [[ "$already" == "1" ]] && continue
  aliases+=("$alias_name"); hosts+=("$host"); users+=("$user"); ports+=("$port")
done < "$inventory"

if [[ ${#aliases[@]} -eq 0 ]]; then
  echo "No hosts parsed from inventory." >&2
  exit 1
fi

# Apply --limit filter.
if [[ "$limit" != "all" && -n "$limit" ]]; then
  declare -a f_aliases=() f_hosts=() f_users=() f_ports=()
  IFS=',' read -ra wanted <<< "$limit"
  for i in "${!aliases[@]}"; do
    for w in "${wanted[@]}"; do
      w="$(echo "$w" | xargs)"
      if [[ "${aliases[$i]}" == "$w" ]]; then
        f_aliases+=("${aliases[$i]}"); f_hosts+=("${hosts[$i]}")
        f_users+=("${users[$i]}"); f_ports+=("${ports[$i]}")
      fi
    done
  done
  aliases=("${f_aliases[@]:-}"); hosts=("${f_hosts[@]:-}")
  users=("${f_users[@]:-}"); ports=("${f_ports[@]:-}")
  if [[ ${#aliases[@]} -eq 0 || -z "${aliases[0]:-}" ]]; then
    echo "No hosts matched --limit '$limit'." >&2
    exit 1
  fi
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
manifest="$workdir/MANIFEST.txt"

{
  echo "Fleet log collection"
  echo "generated_utc: $(date -u '+%Y-%m-%d %H:%M:%S')"
  echo "window: since '${since}'"
  echo "hosts_requested: ${#aliases[@]}"
  echo
} > "$manifest"

# -n is load-bearing, not cosmetic: the docker-logs call below runs inside a
# `while read` over the container list, and without it ssh consumes that list
# from stdin and only the first container is ever collected.
ssh_opts=(-n -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=10
          -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes)

ok_count=0
fail_count=0

for i in "${!aliases[@]}"; do
  alias_name="${aliases[$i]}"
  host="${hosts[$i]}"
  user="${users[$i]}"
  port="${ports[$i]}"
  hostdir="$workdir/$alias_name"
  mkdir -p "$hostdir"

  echo "[logs][$alias_name] collecting from ${user}@${host}:${port}"
  host_ok=1

  # journald: gzip on the host so only compressed bytes cross the wire, and
  # nothing is staged on the host filesystem.
  if ssh "${ssh_opts[@]}" -p "$port" "${user}@${host}" \
      "journalctl --since '${since}' --no-pager 2>/dev/null | gzip -6" \
      > "$hostdir/journal.log.gz" 2>"$hostdir/journal.err"; then
    sz=$(wc -c < "$hostdir/journal.log.gz" | tr -d ' ')
    echo "  ${alias_name}: journal.log.gz ${sz} bytes" >> "$manifest"
    rm -f "$hostdir/journal.err"
  else
    host_ok=0
    err="$(head -c 300 "$hostdir/journal.err" 2>/dev/null | tr '\n' ' ')"
    echo "  ${alias_name}: JOURNAL FAILED — ${err:-ssh error}" >> "$manifest"
    echo "[logs][$alias_name] journal collection failed" >&2
  fi

  # docker: per-container logs. Best effort — a host without docker just yields
  # an empty list and is not an error.
  containers="$(ssh "${ssh_opts[@]}" -p "$port" "${user}@${host}" \
      "command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null || true" \
      2>/dev/null || true)"

  if [[ -n "${containers// }" ]]; then
    while IFS= read -r cname; do
      cname="$(echo "$cname" | tr -d '\r\n' | xargs)"
      [[ -z "$cname" ]] && continue
      # printf, not echo: echo's trailing newline is a non-matching character
      # for `tr -c` and would be rewritten into a stray "_" in every filename.
      safe="$(printf '%s' "$cname" | tr -c 'A-Za-z0-9_.-' '_')"
      # `docker logs --since` rejects journalctl-style phrases ("24 hours ago");
      # it wants a Go duration or RFC3339. Resolve the window to an absolute
      # timestamp on the host, which is Linux and so always has GNU date.
      if ssh "${ssh_opts[@]}" -p "$port" "${user}@${host}" \
          "docker logs --since \"\$(date -u -d '${since}' +%Y-%m-%dT%H:%M:%SZ)\" --timestamps '${cname}' 2>&1 | gzip -6" \
          > "$hostdir/docker-${safe}.log.gz" 2>/dev/null; then
        sz=$(wc -c < "$hostdir/docker-${safe}.log.gz" | tr -d ' ')
        echo "  ${alias_name}: docker-${safe}.log.gz ${sz} bytes" >> "$manifest"
      else
        rm -f "$hostdir/docker-${safe}.log.gz"
        echo "  ${alias_name}: docker logs '${cname}' FAILED" >> "$manifest"
      fi
    done <<< "$containers"
  else
    echo "  ${alias_name}: no docker containers reported" >> "$manifest"
  fi

  # Disk snapshot — cheap, and the thing most likely to explain a bad night.
  ssh "${ssh_opts[@]}" -p "$port" "${user}@${host}" \
      "df -h; echo; df -i; echo; journalctl --disk-usage 2>/dev/null" \
      > "$hostdir/disk.txt" 2>/dev/null || rm -f "$hostdir/disk.txt"

  if [[ "$host_ok" == "1" ]]; then
    ok_count=$((ok_count + 1))
  else
    fail_count=$((fail_count + 1))
  fi
done

{
  echo
  echo "hosts_ok: ${ok_count}"
  echo "hosts_failed: ${fail_count}"
} >> "$manifest"

# Members are already gzipped, so tar without a second compression pass.
tar -cf "$out_archive" -C "$workdir" .
archive_size=$(wc -c < "$out_archive" | tr -d ' ')

echo "[logs] archive: ${out_archive} (${archive_size} bytes), ok=${ok_count} failed=${fail_count}"

# Surface counts for the workflow without making a partial collection fatal:
# a fleet where one host is down should still deliver the other five.
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "hosts_ok=${ok_count}"
    echo "hosts_failed=${fail_count}"
    echo "archive_size=${archive_size}"
  } >> "$GITHUB_OUTPUT"
fi

exit 0
