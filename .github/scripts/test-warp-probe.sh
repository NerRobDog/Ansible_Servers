#!/usr/bin/env bash
# Contract tests for roles/warp_exit/files/warp-probe.sh using fake curl and wg binaries.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
probe="$repo_root/roles/warp_exit/files/warp-probe.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/bin"
cat > "$work/bin/curl" <<'FAKE'
#!/usr/bin/env bash
# Fake curl: honours --output and the URL, answers according to FAKE_CURL_MODE.
out="" url=""
while (($#)); do
  case "$1" in
    --output) out="$2"; shift 2 ;;
    --interface|--max-time|--write-out) shift 2 ;;
    --silent) shift ;;
    *) url="$1"; shift ;;
  esac
done
case "$url" in
  *cdn-cgi/trace*) kind=trace ;;
  *) kind=g204 ;;
esac
case "${FAKE_CURL_MODE}:${kind}" in
  ok:trace|half:trace) printf 'fl=1\nwarp=on\n' > "$out"; printf '200 0.100000' ;;
  nowarp:trace) printf 'fl=1\nwarp=off\n' > "$out"; printf '200 0.100000' ;;
  ok:g204|nowarp:g204) : > "$out"; printf '204 0.050000' ;;
  *) printf '000 5.000000'; exit 28 ;;
esac
FAKE
cat > "$work/bin/wg" <<'FAKE'
#!/usr/bin/env bash
printf 'FAKEpeer=\t%s\n' "${FAKE_WG_HANDSHAKE}"
FAKE
chmod +x "$work/bin/curl" "$work/bin/wg"

fail() { echo "FAIL: $*" >&2; exit 1; }

run_case() {
  local mode="$1" handshake="$2" dir="$work/case-$1-$2"
  mkdir -p "$dir"
  FAKE_CURL_MODE="$mode" FAKE_WG_HANDSHAKE="$handshake" \
    WARP_PROBE_CURL="$work/bin/curl" WARP_PROBE_WG="$work/bin/wg" \
    WARP_PROBE_OUT="$dir/warp.prom" WARP_PROBE_NOW=1060 \
    bash "$probe"
  echo "$dir"
}

metric() { awk -v name="$1" '$1 == name { print $2 }' "$2/warp.prom"; }

expect_num() {
  local name="$1" dir="$2" want="$3" got
  got="$(metric "$name" "$dir")"
  [[ -n "$got" ]] || fail "$name missing in $dir/warp.prom"
  awk -v g="$got" -v w="$want" 'BEGIN { d = g - w; if (d < 0) d = -d; exit !(d < 0.000001) }' \
    || fail "$name = $got, want $want ($dir)"
}

expect_nan() {
  local got
  got="$(metric "$1" "$2")"
  [[ "$got" == "NaN" ]] || fail "$1 = '$got', want NaN ($2)"
}

check_format() {
  local dir="$1" extra
  grep -vE '^# (HELP|TYPE) warp_[a-z_]+ .+$|^warp_[a-z_]+ (NaN|[0-9]+(\.[0-9]+)?)$' "$dir/warp.prom" \
    && fail "unexpected lines in $dir/warp.prom"
  extra="$(find "$dir" -type f ! -name warp.prom | wc -l | tr -d ' ')"
  [[ "$extra" == 0 ]] || fail "temporary files left next to warp.prom in $dir"
}

# Runs the probe against an $out path that should make it fail before ever
# reaching curl's results, and returns the path to its captured stderr. Uses
# its own error handling (rather than run_case) because the probe is expected
# to exit non-zero here, which would otherwise trip this test's `set -e`.
run_failing_case() {
  local label="$1" out_path="$2" err="$work/stderr-$1" rc=0
  FAKE_CURL_MODE=ok FAKE_WG_HANDSHAKE=1000 \
    WARP_PROBE_CURL="$work/bin/curl" WARP_PROBE_WG="$work/bin/wg" \
    WARP_PROBE_OUT="$out_path" WARP_PROBE_NOW=1060 \
    bash "$probe" 2>"$err" || rc=$?
  [[ "$rc" -ne 0 ]] || fail "expected non-zero exit for $label (out=$out_path), got 0"
  echo "$err"
}

dir="$(run_case ok 1000)"
expect_num warp_probe_success_ratio "$dir" 1
expect_num warp_probe_latency_avg_seconds "$dir" 0.075
expect_num warp_status_on "$dir" 1
expect_num warp_handshake_age_seconds "$dir" 60
expect_num warp_probe_last_run_timestamp_seconds "$dir" 1060
check_format "$dir"
echo "PASS: all requests succeed"

dir="$(run_case half 1000)"
expect_num warp_probe_success_ratio "$dir" 0.5
expect_num warp_probe_latency_avg_seconds "$dir" 0.1
expect_num warp_status_on "$dir" 1
check_format "$dir"
echo "PASS: half of the requests hang"

dir="$(run_case down 0)"
expect_num warp_probe_success_ratio "$dir" 0
expect_nan warp_probe_latency_avg_seconds "$dir"
expect_num warp_status_on "$dir" 0
expect_nan warp_handshake_age_seconds "$dir"
check_format "$dir"
echo "PASS: tunnel down, no handshake"

dir="$(run_case nowarp 1000)"
expect_num warp_probe_success_ratio "$dir" 0.5
expect_num warp_status_on "$dir" 0
check_format "$dir"
echo "PASS: trace without warp=on is not counted as success"

dir="$work/case-outdir"
mkdir -p "$dir"
out_path="$dir/warp.prom"
mkdir -p "$out_path"
err="$(run_failing_case outdir "$out_path")"
grep -q 'is a directory' "$err" || fail "expected a directory-refusal message in $err, got: $(cat "$err")"
extra="$(find "$dir" -type f | wc -l | tr -d ' ')"
[[ "$extra" == 0 ]] || fail "leftover files under $dir: $(find "$dir" -type f)"
echo "PASS: refuses to move metrics into an existing directory at \$WARP_PROBE_OUT, no debris left"

dir="$work/case-missingdir"
mkdir -p "$dir"
out_path="$dir/missing-subdir/warp.prom"
err="$(run_failing_case missingdir "$out_path")"
grep -q 'warp-probe: mktemp' "$err" || fail "expected a warp-probe mktemp failure message in $err, got: $(cat "$err")"
extra="$(find "$dir" -type f | wc -l | tr -d ' ')"
[[ "$extra" == 0 ]] || fail "leftover files under $dir: $(find "$dir" -type f)"
echo "PASS: mktemp failure (missing \$WARP_PROBE_OUT directory) exits non-zero with a clear message, no debris left"

echo "All warp-probe contract tests passed."
