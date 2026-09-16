#!/usr/bin/env bash
# Renders fleet-alerts.yml.j2 and unit-tests the warp-exit rules with promtool.
# PROMTOOL may be a command line, e.g. a docker run wrapper when promtool is not installed locally.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
read -r -a promtool_cmd <<< "${PROMTOOL:-promtool}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

python3 - "$repo_root/roles/monitoring_stack/templates/fleet-alerts.yml.j2" "$work/fleet-alerts.yml" <<'PY'
import sys

import jinja2

source, target = sys.argv[1], sys.argv[2]
env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
with open(source, encoding="utf-8") as handle:
    rendered = env.from_string(handle.read()).render()
with open(target, "w", encoding="utf-8") as handle:
    handle.write(rendered)
PY

cp "$repo_root/.github/scripts/fixtures/warp-alerts.test.yml" "$work/warp-alerts.test.yml"
"${promtool_cmd[@]}" check rules "$work/fleet-alerts.yml"
"${promtool_cmd[@]}" test rules "$work/warp-alerts.test.yml"
echo "All warp-exit alert rule tests passed."
