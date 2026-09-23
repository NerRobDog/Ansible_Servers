#!/usr/bin/env bash
# The ssh_lockdown role must leave sshd with password auth and root login actually off.
# sshd keeps the first value it reads for a keyword, and the Include of sshd_config.d/*.conf
# is expanded in lexical order, so a drop-in named 99-* loses to the 50-cloud-init.conf
# that Ubuntu cloud images ship with "PasswordAuthentication yes". That is how tw-germ-1
# ended up with the lockdown file in place and password logins still accepted.
# This test runs the role against a container with a real sshd and reads the effective
# config with `sshd -T`, which is what the daemon will enforce.
set -euo pipefail

cd "$(dirname "$0")/../.."

image="${SSH_LOCKDOWN_TEST_IMAGE:-python:3.12-slim}"
name="ssh-lockdown-test-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

export ANSIBLE_ROLES_PATH="$PWD/roles" ANSIBLE_HOST_KEY_CHECKING=false
export ANSIBLE_INVENTORY_UNPARSED_WARNING=false ANSIBLE_LOCALHOST_WARNING=false

in_box() { docker exec "$name" sh -c "$1"; }

run_play() {
  ansible-playbook -i "${name}," -c community.docker.docker \
    -e ansible_python_interpreter=/usr/local/bin/python3 \
    .github/scripts/fixtures/ssh-lockdown-test.yml "$@"
}

effective() { in_box "/usr/sbin/sshd -T" | awk -v k="$1" '$1 == k {print $2}'; }

docker run -d --name "$name" "$image" sleep infinity >/dev/null
in_box "apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server >/dev/null"
in_box "mkdir -p /run/sshd /etc/ssh/sshd_config.d"
# What an Ubuntu cloud image ships, plus the drop-in the old role version left behind.
in_box "printf 'PasswordAuthentication yes\n' > /etc/ssh/sshd_config.d/50-cloud-init.conf"
in_box "printf 'PasswordAuthentication no\nPermitRootLogin no\n' > /etc/ssh/sshd_config.d/99-ansible-hardening.conf"
[[ "$(effective passwordauthentication)" == "yes" ]] || fail "fixture: expected cloud-init to win before the role runs"

# Dry run (workflow check_mode=true): passes and leaves sshd untouched.
out="$(run_play --check)" || { echo "$out" >&2; fail "check mode: play failed"; }
[[ "$(effective passwordauthentication)" == "yes" ]] || fail "check mode: sshd config was changed"
echo "PASS: check mode passes and changes nothing"

# Cloud-init drop-in present: the role must still turn password auth and root login off.
out="$(run_play)" || { echo "$out" >&2; fail "lockdown: play failed"; }
[[ "$(effective passwordauthentication)" == "no" ]] || fail "lockdown: sshd -T still reports passwordauthentication $(effective passwordauthentication)"
[[ "$(effective permitrootlogin)" == "no" ]] || fail "lockdown: sshd -T still reports permitrootlogin $(effective permitrootlogin)"
in_box "test ! -e /etc/ssh/sshd_config.d/99-ansible-hardening.conf" || fail "lockdown: legacy 99-ansible-hardening.conf was left in place"
echo "PASS: password auth and root login are off despite 50-cloud-init.conf"

# Converged host: a second run changes nothing.
out="$(run_play)" || { echo "$out" >&2; fail "idempotence: play failed"; }
grep -Eq 'changed=0 ' <<< "$out" || { echo "$out" >&2; fail "idempotence: second run reported a change"; }
echo "PASS: second run is idempotent"

# Something that still overrides the drop-in must fail the run, not pass silently.
in_box "printf 'PasswordAuthentication yes\n' > /etc/ssh/sshd_config.d/00-aaa-override.conf"
if out="$(run_play)"; then
  echo "$out" >&2
  fail "guard: play passed while sshd -T reports passwordauthentication $(effective passwordauthentication)"
fi
grep -q 'overrides 00-ansible-hardening.conf' <<< "$out" || { echo "$out" >&2; fail "guard: play failed for another reason"; }
echo "PASS: an overriding drop-in fails the run"
