#!/usr/bin/env bash
# The tailscale role pins the tailnet hostname to the fleet alias. Providers set the OS
# hostname to internal IDs (6019769891-arcoknq, bumpy-red), and Tailscale registers the
# node under that name unless told otherwise.
set -euo pipefail

cd "$(dirname "$0")/../.."

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

cat > "$work/bin/tailscale" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  status)
    printf '{"BackendState":"Running","Self":{"HostName":"%s","DNSName":"x.example.ts.net."},"Peer":null}\n' \
      "$(cat "$FAKE_TS_STATE")"
    ;;
  set)
    echo "$*" >> "$FAKE_TS_CALLS"
    for arg in "$@"; do
      case "$arg" in --hostname=*) printf '%s' "${arg#--hostname=}" > "$FAKE_TS_STATE" ;; esac
    done
    ;;
  *) echo "unexpected: $*" >&2; exit 2 ;;
esac
FAKE
chmod +x "$work/bin/tailscale"

export FAKE_TS_STATE="$work/state" FAKE_TS_CALLS="$work/calls"
export PATH="$work/bin:$PATH" ANSIBLE_ROLES_PATH="$PWD/roles" ANSIBLE_LOCALHOST_WARNING=false ANSIBLE_INVENTORY_UNPARSED_WARNING=false

fail() { echo "FAIL: $*" >&2; exit 1; }

run_play() {
  local alias="$1"; shift
  ansible-playbook -i "${alias}," -c local \
    -e "ansible_python_interpreter=$(command -v python3)" -e "fixture_openwrt=$openwrt" \
    .github/scripts/fixtures/tailscale-hostname-test.yml "$@"
}

calls() { cat "$FAKE_TS_CALLS" 2>/dev/null || true; }

# The server branch uses command, the OpenWrt branch raw; both must behave the same.
for openwrt in false true; do
  flavor="$([[ "$openwrt" == true ]] && echo openwrt || echo server)"
  # Drift: provider hostname is renamed to the alias, with underscores turned into hyphens.
  printf 'spb-1' > "$FAKE_TS_STATE"; : > "$FAKE_TS_CALLS"
  out="$(run_play tw_spb_1)" || { echo "$out" >&2; fail "[$flavor] drift: play failed"; }
  [[ "$(calls)" == "set --hostname=tw-spb-1" ]] || fail "[$flavor] drift: expected one rename to tw-spb-1, got: $(calls)"
  grep -Eq 'changed=1 ' <<< "$out" || fail "[$flavor] drift: play did not report changed=1"
  echo "PASS [$flavor]: drifted hostname is renamed to the alias"

  # Converged: second run changes nothing.
  out="$(run_play tw_spb_1)" || { echo "$out" >&2; fail "[$flavor] idempotence: play failed"; }
  [[ "$(calls)" == "set --hostname=tw-spb-1" ]] || fail "[$flavor] idempotence: unexpected extra call: $(calls)"
  grep -Eq 'changed=0 ' <<< "$out" || fail "[$flavor] idempotence: play reported a change"
  echo "PASS [$flavor]: matching hostname is left alone"

  # Check mode reports the rename but does not perform it.
  printf 'bumpy-red-1' > "$FAKE_TS_STATE"; : > "$FAKE_TS_CALLS"
  run_play ae-us-1 --check > "$work/check.out" 2>&1 || { cat "$work/check.out" >&2; fail "[$flavor] check mode: play failed"; }
  [[ -z "$(calls)" ]] || fail "[$flavor] check mode: tailscale set was called: $(calls)"
  [[ "$(cat "$FAKE_TS_STATE")" == "bumpy-red-1" ]] || fail "[$flavor] check mode: hostname changed"
  echo "PASS [$flavor]: check mode does not rename"

  # An explicit empty hostname opts the host out.
  run_play ae-us-1 -e tailscale_hostname= > "$work/optout.out" 2>&1 || { cat "$work/optout.out" >&2; fail "[$flavor] opt-out: play failed"; }
  [[ -z "$(calls)" ]] || fail "[$flavor] opt-out: tailscale set was called: $(calls)"
  echo "PASS [$flavor]: empty tailscale_hostname opts out"

  # An invalid name fails before touching the tailnet.
  if run_play ae-us-1 -e 'tailscale_hostname=Bad_Name' > "$work/invalid.out" 2>&1; then
    fail "[$flavor] invalid: play succeeded"
  fi
  grep -q "not a valid" "$work/invalid.out" || fail "[$flavor] invalid: missing validation message"
  [[ -z "$(calls)" ]] || fail "[$flavor] invalid: tailscale set was called: $(calls)"
  echo "PASS [$flavor]: invalid hostname is rejected"
done

echo "All tailscale hostname tests passed."
