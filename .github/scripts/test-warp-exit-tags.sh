#!/usr/bin/env bash
# Guards the one mistake that would silently rotate a WARP exit IP: re-registration must
# run only under --tags warp_reregister, never under the tags a normal deploy uses.
# A task tagged `never` still runs when ANY of its other tags is requested, so a role-level
# tag in playbook.yml inherited by the re-registration tasks would break this.
set -euo pipefail

cd "$(dirname "$0")/../.."

reregister_marker="Back up the current WARP registration"
verify_marker="Verify traffic leaves through WARP"

list_tasks() {
  if [[ -n "$1" ]]; then
    ansible-playbook -i hosts.example.ini playbook.yml --list-tasks --tags "$1"
  else
    ansible-playbook -i hosts.example.ini playbook.yml --list-tasks
  fi
}

fail() { echo "FAIL: $*" >&2; exit 1; }

for tags in "" warp remnawave monitoring "warp,monitoring" "remnawave,node"; do
  listing="$(list_tasks "$tags")"
  if grep -qF "$reregister_marker" <<< "$listing"; then
    fail "--tags '${tags:-<none>}' would run WARP re-registration"
  fi
  echo "PASS: --tags '${tags:-<none>}' does not include re-registration"
done

listing="$(list_tasks warp)"
grep -qF "$verify_marker" <<< "$listing" || fail "--tags warp does not include the tunnel verification"
echo "PASS: --tags warp includes the tunnel verification"

listing="$(list_tasks warp_reregister)"
grep -qF "$reregister_marker" <<< "$listing" || fail "--tags warp_reregister does not include re-registration"
grep -qF "Install WireGuard tools" <<< "$listing" && fail "--tags warp_reregister must not run the install path"
echo "PASS: --tags warp_reregister runs re-registration only"

echo "All warp_exit tag selection tests passed."
