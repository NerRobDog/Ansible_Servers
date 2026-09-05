# mihomo Config Gate Implementation Plan (Phase A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repository the source of truth for the mihomo routing config, and gate every change to it behind syntax, referential-integrity, routing and regression checks that run on pull requests.

**Architecture:** The panel's live MIHOMO `Default` template is captured verbatim into `config/mihomo/default.template.yaml`. A candidate builder assembles a runnable mihomo config offline by splicing real proxies from a rendered subscription into the template and stripping the one Remnawave-specific key; `include-all`, `exclude-filter` and `filter` are native mihomo fields and need no translation. The candidate is then checked by four layers — `mihomo -t`, a referential-integrity linter, a live routing probe run in Docker, and a diff against a checked-in expectations file. Nothing in this phase touches the panel or any router.

**Tech Stack:** Python 3.12 (stdlib + PyYAML, no pytest — this repo runs plain `assert`-style contract test scripts), Docker (`metacubex/mihomo`), GitHub Actions.

**Scope note:** This is Phase A of epic `as-5g2`. Phase B (panel PATCH, canary rollout to `wrt-zmit`, auto-rollback) is a separate plan and must not be started until Phase A is merged and green.

**Spec:** `docs/superpowers/specs/2026-08-21-mihomo-config-gate-design.md` (Russian: `...-design.ru.md`)

---

## File Structure

| Path | Responsibility |
|---|---|
| `config/mihomo/default.template.yaml` | Canonical, byte-faithful capture of the live MIHOMO `Default` template. Data, not code. |
| `config/mihomo/README.md` | What the file is, how it relates to the panel, why it is excluded from yamllint. |
| `config/mihomo/routing-expectations.yaml` | Golden file: domain → expected proxy-group. Changing a route means changing this in the same commit. |
| `.github/scripts/mihomo_panel_api.py` | Base64 envelope encode/decode + read-only template fetch. Pure helpers separated from network. |
| `.github/scripts/mihomo_candidate.py` | Pure transform: template + real proxies → runnable mihomo config. |
| `.github/scripts/mihomo_lint.py` | Pure analysis: referential integrity of a parsed config. |
| `.github/scripts/mihomo_routing.py` | Log parser (pure) + Docker harness driver (impure). |
| `.github/scripts/mihomo_gate.py` | Orchestrator: runs all four layers, writes a Markdown report. |
| `.github/scripts/test-mihomo-candidate.py` | Contract tests for the candidate builder. |
| `.github/scripts/test-mihomo-lint.py` | Contract tests for the linter. |
| `.github/scripts/test-mihomo-routing.py` | Contract tests for the log parser and route comparison. |
| `.github/workflows/mihomo-config-gate.yml` | PR gate workflow. |
| `.yamllint` | Modified: ignore `config/mihomo/`. |
| `.github/workflows/ansible-ci.yml` | Modified: run the three new contract test scripts. |

The three pure modules (`mihomo_candidate`, `mihomo_lint`, the parser half of `mihomo_routing`) carry all the logic that can break silently, and all of it is unit-testable without Docker or network. The impure parts stay thin on purpose.

---

## Task 1: Capture the live template as canonical

The single most dangerous commit in this epic. It defines "correct" for everything that follows, so it must be a faithful capture and nothing else — no reformatting, no tidying, no "while I'm here".

**Files:**
- Create: `config/mihomo/default.template.yaml`
- Create: `config/mihomo/README.md`
- Modify: `.yamllint`

- [ ] **Step 1: Fetch the live template**

The panel API token is at `~/.remnawave_api_key` locally. The MIHOMO `Default` template uuid is `1ef0346d-7989-4e50-88e6-bbe2b99672fc`.

```bash
mkdir -p config/mihomo
TOKEN=$(tr -d '\n' < ~/.remnawave_api_key)
curl -s --max-time 25 -H "Authorization: Bearer $TOKEN" \
  "https://ru.watchd0g.dev/api/subscription-templates/1ef0346d-7989-4e50-88e6-bbe2b99672fc" \
| python3 -c "
import sys, json, base64
payload = json.load(sys.stdin)['response']
data = base64.b64decode(payload['encodedTemplateYaml'])
open('config/mihomo/default.template.yaml','wb').write(data)
print(f'wrote {len(data)} bytes')
"
```

Expected: `wrote 34020 bytes` (the exact number may differ if the template changed since 2026-08-21; that is fine, faithfulness matters, not the byte count).

- [ ] **Step 2: Verify the capture round-trips**

This proves the file on disk re-encodes to exactly what the panel holds. If it does not, something mangled encoding or line endings and the capture is not faithful.

```bash
TOKEN=$(tr -d '\n' < ~/.remnawave_api_key)
curl -s --max-time 25 -H "Authorization: Bearer $TOKEN" \
  "https://ru.watchd0g.dev/api/subscription-templates/1ef0346d-7989-4e50-88e6-bbe2b99672fc" \
| python3 -c "
import sys, json, base64
live = base64.b64decode(json.load(sys.stdin)['response']['encodedTemplateYaml'])
disk = open('config/mihomo/default.template.yaml','rb').read()
assert live == disk, f'MISMATCH: live={len(live)} disk={len(disk)}'
print('OK: capture is byte-identical to the live template')
"
```

Expected: `OK: capture is byte-identical to the live template`

- [ ] **Step 3: Exclude the capture from yamllint**

`yamllint .` runs over the whole repo in `ansible-ci`. The live template produces 51 problems (indentation and a missing document start). Reformatting it would break the faithfulness this task exists to guarantee, so the directory is ignored instead.

Modify `.yamllint`, adding one line to the existing `ignore:` block:

```yaml
ignore: |
  .venv/
  .ansible/
  fleet.yml
  config/mihomo/
```

- [ ] **Step 4: Verify yamllint passes**

Run: `yamllint .`
Expected: no output, exit code 0.

- [ ] **Step 5: Write the README**

Create `config/mihomo/README.md`:

```markdown
# mihomo routing config

`default.template.yaml` is the canonical copy of the MIHOMO `Default`
subscription template served by the panel at `ru.watchd0g.dev`
(uuid `1ef0346d-7989-4e50-88e6-bbe2b99672fc`). Routers download the rendered
form of it from `no.watchd0g.dev/<token>` as their OpenClash profile.

**This repository is the source of truth.** It did not used to be: until
2026-08-21 every fix was applied directly through the panel API, and the repo
files (`mihomo-remnawave-davoyan-rubypass-v2.*.yaml`) were detached drafts that
had drifted behind the live template by two fixes. Editing the template through
the panel web UI reintroduces that drift — change this file instead.

## Why it is excluded from yamllint

`.yamllint` ignores this directory. The file is a verbatim capture, and the
panel's formatting produces 51 yamllint findings. Reformatting it to satisfy the
linter would mean the repo no longer holds what the panel holds, which is the
one property this file exists to have.

## The template is not directly runnable

It carries a stub `proxies:` list and one Remnawave-specific key
(`remnawave:` inside proxy-groups) that the panel fills in per subscriber.
`include-all`, `exclude-filter` and `filter` are *native mihomo* fields and need
no translation. `.github/scripts/mihomo_candidate.py` assembles a runnable
config by splicing in real proxies and dropping the `remnawave:` key.

## Related

- `routing-expectations.yaml` — golden file asserting where each domain routes.
- `docs/superpowers/specs/2026-08-21-mihomo-config-gate-design.md` — design.
```

- [ ] **Step 6: Commit**

```bash
git add config/mihomo/default.template.yaml config/mihomo/README.md .yamllint
git commit -m "feat(mihomo): capture live Default template as canonical config

The repo was not the source of truth: the live panel template was ahead by the
2026-08-18 YouTube alias groups and the de-matrix-1 TURN bypass rule, because
push_mihomo_template.py patches the live template rather than uploading a file.
Capture it verbatim so the first gate diff is honest.

Excluded from yamllint deliberately - reformatting would break faithfulness."
```

---

## Task 2: Panel API envelope helpers

**Files:**
- Create: `.github/scripts/mihomo_panel_api.py`
- Test: `.github/scripts/test-mihomo-candidate.py` (envelope tests live here to avoid a fourth test script for two functions)

- [ ] **Step 1: Write the failing test**

Create `.github/scripts/test-mihomo-candidate.py`:

```python
#!/usr/bin/env python3
"""Contract tests for mihomo_panel_api.py and mihomo_candidate.py."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_panel_api  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_decode_unwraps_response_envelope() -> None:
    body = "mixed-port: 7890\n"
    payload = {"response": {"encodedTemplateYaml": base64.b64encode(body.encode()).decode()}}
    assert_true(mihomo_panel_api.decode_template_payload(payload) == body, "response envelope not unwrapped")


def test_decode_accepts_bare_payload() -> None:
    body = "mode: rule\n"
    payload = {"encodedTemplateYaml": base64.b64encode(body.encode()).decode()}
    assert_true(mihomo_panel_api.decode_template_payload(payload) == body, "bare payload not decoded")


def test_decode_rejects_empty_template() -> None:
    try:
        mihomo_panel_api.decode_template_payload({"response": {"uuid": "x"}})
    except ValueError as exc:
        assert_true("uuid" in str(exc), "error should name the keys it did see")
        return
    raise AssertionError("empty encodedTemplateYaml must raise")


def test_roundtrip_preserves_non_ascii() -> None:
    # Group names are emoji-heavy ("📺 YouTube"); a latin-1 slip here would
    # corrupt every rule target in the config.
    body = 'rules:\n- MATCH,🌍 Остальные сайты\n'
    encoded = mihomo_panel_api.encode_template_yaml(body)
    decoded = mihomo_panel_api.decode_template_payload({"encodedTemplateYaml": encoded})
    assert_true(decoded == body, "non-ASCII round-trip corrupted the body")


def main() -> int:
    tests = [
        test_decode_unwraps_response_envelope,
        test_decode_accepts_bare_payload,
        test_decode_rejects_empty_template,
        test_roundtrip_preserves_non_ascii,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All mihomo candidate contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 .github/scripts/test-mihomo-candidate.py`
Expected: `ModuleNotFoundError: No module named 'mihomo_panel_api'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/scripts/mihomo_panel_api.py`:

```python
#!/usr/bin/env python3
"""Read-only client for the Remnawave subscription-template API.

The base64 envelope helpers are kept separate from the HTTP call so the part
that actually breaks - encoding of emoji-heavy group names - is unit-testable
without a panel token.

Phase A never writes. The PATCH path lands with the canary rollout in Phase B.
"""

from __future__ import annotations

import base64
import json
import urllib.request
from typing import Any

MIHOMO_DEFAULT_UUID = "1ef0346d-7989-4e50-88e6-bbe2b99672fc"


def decode_template_payload(payload: dict[str, Any]) -> str:
    """Extract the YAML body from a subscription-template API response."""
    inner = payload.get("response", payload)
    encoded = inner.get("encodedTemplateYaml")
    if not encoded:
        raise ValueError(f"encodedTemplateYaml is empty; payload keys: {sorted(inner)}")
    return base64.b64decode(encoded).decode("utf-8")


def encode_template_yaml(text: str) -> str:
    """Encode a YAML body for the API's encodedTemplateYaml field."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def fetch_template(base_url: str, token: str, uuid: str = MIHOMO_DEFAULT_UUID) -> str:
    """GET one subscription template and return its YAML body."""
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/subscription-templates/{uuid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return decode_template_payload(payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 .github/scripts/test-mihomo-candidate.py`
Expected: four `PASS:` lines and `All mihomo candidate contract tests passed.`

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/mihomo_panel_api.py .github/scripts/test-mihomo-candidate.py
git commit -m "feat(mihomo): add panel template API envelope helpers"
```

---

## Task 3: Candidate builder

**Files:**
- Create: `.github/scripts/mihomo_candidate.py`
- Modify: `.github/scripts/test-mihomo-candidate.py`

- [ ] **Step 1: Write the failing tests**

Append to `.github/scripts/test-mihomo-candidate.py`, before `def main()`:

```python
import mihomo_candidate  # noqa: E402


def sample_template() -> dict:
    return {
        "mixed-port": 7890,
        "allow-lan": False,
        "tun": {"enable": True, "stack": "system"},
        "proxies": [{"name": "🇷🇺 Без VPN", "type": "direct"}],
        "proxy-groups": [
            {
                "name": "🌍 Остальные сайты",
                "type": "select",
                "remnawave": {"include-proxies": False},
                "proxies": ["DIRECT"],
            },
            {"name": "📺 YouTube", "type": "select", "include-all": True},
        ],
        "rules": ["MATCH,🌍 Остальные сайты"],
    }


def real_proxies() -> list[dict]:
    return [
        {"name": "🇳🇱 Netherlands-2", "type": "vless", "server": "nl.example", "port": 443},
        {"name": "🇺🇸 USA-2", "type": "vless", "server": "us.example", "port": 443},
    ]


def test_candidate_replaces_proxies() -> None:
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies())
    names = [p["name"] for p in result["proxies"]]
    assert_true(names == ["🇳🇱 Netherlands-2", "🇺🇸 USA-2"], f"stub proxies not replaced: {names}")


def test_candidate_strips_remnawave_key() -> None:
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies())
    leftovers = [g["name"] for g in result["proxy-groups"] if "remnawave" in g]
    assert_true(not leftovers, f"remnawave key survived in: {leftovers}")


def test_candidate_disables_tun() -> None:
    # TUN needs NET_ADMIN and a real device; the harness is userspace-only.
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies())
    assert_true(result["tun"]["enable"] is False, "tun must be disabled in the harness")
    assert_true(result["tun"]["stack"] == "system", "unrelated tun settings must survive")


def test_candidate_forces_allow_lan() -> None:
    # With allow-lan false mihomo listens on loopback INSIDE the container, so a
    # published port goes nowhere and curl fails in 3ms with http=000.
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies())
    assert_true(result["allow-lan"] is True, "allow-lan must be forced on")
    assert_true(result["bind-address"] == "*", "bind-address must be wildcard")


def test_candidate_sets_controller() -> None:
    result = mihomo_candidate.build_candidate(sample_template(), real_proxies(), secret="s3cr3t")
    assert_true(result["external-controller"] == "0.0.0.0:9099", "controller not set")
    assert_true(result["secret"] == "s3cr3t", "secret not set")
    assert_true(result["log-level"] == "debug", "debug log level is required to read rule matches")


def test_candidate_does_not_mutate_input() -> None:
    template = sample_template()
    mihomo_candidate.build_candidate(template, real_proxies())
    assert_true("remnawave" in template["proxy-groups"][0], "input template was mutated")
    assert_true(template["tun"]["enable"] is True, "input template tun was mutated")
```

Add the six new test names to the `tests` list in `main()`:

```python
        test_candidate_replaces_proxies,
        test_candidate_strips_remnawave_key,
        test_candidate_disables_tun,
        test_candidate_forces_allow_lan,
        test_candidate_sets_controller,
        test_candidate_does_not_mutate_input,
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 .github/scripts/test-mihomo-candidate.py`
Expected: `ModuleNotFoundError: No module named 'mihomo_candidate'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/scripts/mihomo_candidate.py`:

```python
#!/usr/bin/env python3
"""Assemble a runnable mihomo config from a Remnawave subscription template.

The template is not directly runnable: it carries a stub `proxies:` list the
panel fills per subscriber, and one panel-specific key (`remnawave:`) inside
proxy-groups. Everything else - notably `include-all`, `exclude-filter` and
`filter` - is native mihomo and needs no translation, which is what makes
offline testing possible at all.

Verified 2026-08-21: the output of this transform passes `mihomo -t`.
"""

from __future__ import annotations

import copy
from typing import Any

HARNESS_CONTROLLER = "0.0.0.0:9099"
HARNESS_SECRET = "mihomo-gate"


def build_candidate(
    template: dict[str, Any],
    proxies: list[dict[str, Any]],
    *,
    controller: str = HARNESS_CONTROLLER,
    secret: str = HARNESS_SECRET,
) -> dict[str, Any]:
    """Return a runnable copy of `template` with real proxies spliced in."""
    config = copy.deepcopy(template)
    config["proxies"] = copy.deepcopy(proxies)

    for group in config.get("proxy-groups") or []:
        group.pop("remnawave", None)

    # TUN needs NET_ADMIN and a real tun device; the harness runs userspace-only.
    tun = config.setdefault("tun", {})
    tun["enable"] = False

    # Without this mihomo binds only loopback INSIDE the container, so the
    # published port leads nowhere and curl fails in 3ms with http=000 - which
    # reads like a config error and is not one.
    config["allow-lan"] = True
    config["bind-address"] = "*"

    config["external-controller"] = controller
    config["secret"] = secret
    # Rule matches are only logged at debug level, and they are the whole point.
    config["log-level"] = "debug"

    return config
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 .github/scripts/test-mihomo-candidate.py`
Expected: ten `PASS:` lines and the summary line.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/mihomo_candidate.py .github/scripts/test-mihomo-candidate.py
git commit -m "feat(mihomo): build runnable candidate config offline

Splices real proxies into the template and drops the panel-specific remnawave
key. Also disables tun and forces allow-lan: both are harness requirements
found by hitting them - the second one fails as http=000 in 3ms and looks like
a config error."
```

---

## Task 4: Referential-integrity linter

Catches the class `mihomo -t` cannot: a rule pointing at a group that no longer exists, a `RULE-SET` naming an undeclared provider, an uncompilable filter regex.

**Files:**
- Create: `.github/scripts/mihomo_lint.py`
- Test: `.github/scripts/test-mihomo-lint.py`

- [ ] **Step 1: Write the failing test**

Create `.github/scripts/test-mihomo-lint.py`:

```python
#!/usr/bin/env python3
"""Contract tests for mihomo_lint.py."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_lint  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def healthy_config() -> dict:
    return {
        "proxies": [{"name": "DNS-OUT", "type": "dns"}, {"name": "🇳🇱 NL", "type": "vless"}],
        "proxy-groups": [
            {"name": "📺 YouTube", "type": "select", "proxies": ["🇳🇱 NL"]},
            {"name": "🌍 Остальные", "type": "select", "proxies": ["📺 YouTube", "DIRECT"]},
        ],
        "rule-providers": {"youtube": {"type": "http", "url": "https://example/yt.yaml"}},
        "rules": [
            "DST-PORT,53,DNS-OUT",
            "RULE-SET,youtube,📺 YouTube",
            "MATCH,🌍 Остальные",
        ],
    }


def test_healthy_config_has_no_errors() -> None:
    errors, _ = mihomo_lint.lint_config(healthy_config())
    assert_true(errors == [], f"healthy config reported errors: {errors}")


def test_dangling_ruleset_reference_is_an_error() -> None:
    config = healthy_config()
    config["rules"].insert(0, "RULE-SET,does-not-exist,📺 YouTube")
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(any("does-not-exist" in e for e in errors), f"dangling RULE-SET not caught: {errors}")


def test_unknown_rule_target_is_an_error() -> None:
    config = healthy_config()
    config["rules"].insert(0, "RULE-SET,youtube,📺 Ghost Group")
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(any("Ghost Group" in e for e in errors), f"unknown target not caught: {errors}")


def test_proxy_is_a_valid_rule_target() -> None:
    # DNS-OUT is a proxy (type: dns), not a group. Treating only groups as
    # valid targets would flag the very first rule of the real config.
    errors, _ = mihomo_lint.lint_config(healthy_config())
    assert_true(not any("DNS-OUT" in e for e in errors), f"proxy target wrongly rejected: {errors}")


def test_logic_rule_with_nested_parens_is_parsed() -> None:
    # A naive split(',') tears this rule apart and reports garbage targets.
    config = healthy_config()
    config["rules"].insert(
        0, "AND,((RULE-SET,youtube),(NETWORK,udp),(DST-PORT,50000-50100)),📺 YouTube"
    )
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(errors == [], f"logic rule mis-parsed: {errors}")


def test_no_resolve_modifier_is_ignored_when_finding_target() -> None:
    config = healthy_config()
    config["rules"].insert(0, "IP-CIDR,193.233.75.48/32,DIRECT,no-resolve")
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(errors == [], f"no-resolve modifier confused target detection: {errors}")


def test_dangling_group_member_is_an_error() -> None:
    config = healthy_config()
    config["proxy-groups"][0]["proxies"].append("🇩🇪 Missing")
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(any("Missing" in e for e in errors), f"dangling group member not caught: {errors}")


def test_bad_exclude_filter_regex_is_an_error() -> None:
    config = healthy_config()
    config["proxy-groups"][0]["exclude-filter"] = "(?i)(unclosed"
    errors, _ = mihomo_lint.lint_config(config)
    assert_true(any("exclude-filter" in e for e in errors), f"bad regex not caught: {errors}")


def test_orphan_group_is_a_warning_not_an_error() -> None:
    config = healthy_config()
    config["proxy-groups"].append({"name": "🕳 Unused", "type": "select", "proxies": ["DIRECT"]})
    errors, warnings = mihomo_lint.lint_config(config)
    assert_true(errors == [], f"orphan group must not be a hard error: {errors}")
    assert_true(any("Unused" in w for w in warnings), f"orphan group not warned about: {warnings}")


def test_proxy_group_is_allowlisted_as_orphan() -> None:
    # Remnawave surfaces a group named PROXY that no rule targets. It is not a
    # defect and must not generate noise on every run.
    config = healthy_config()
    config["proxy-groups"].append({"name": "PROXY", "type": "select", "proxies": ["DIRECT"]})
    _, warnings = mihomo_lint.lint_config(config)
    assert_true(not any("PROXY" in w for w in warnings), f"PROXY should be allowlisted: {warnings}")


def test_unused_provider_is_a_warning() -> None:
    config = healthy_config()
    config["rule-providers"]["never-used"] = {"type": "http", "url": "https://example/x.yaml"}
    errors, warnings = mihomo_lint.lint_config(config)
    assert_true(errors == [], f"unused provider must not be a hard error: {errors}")
    assert_true(any("never-used" in w for w in warnings), f"unused provider not warned: {warnings}")


def main() -> int:
    tests = [
        test_healthy_config_has_no_errors,
        test_dangling_ruleset_reference_is_an_error,
        test_unknown_rule_target_is_an_error,
        test_proxy_is_a_valid_rule_target,
        test_logic_rule_with_nested_parens_is_parsed,
        test_no_resolve_modifier_is_ignored_when_finding_target,
        test_dangling_group_member_is_an_error,
        test_bad_exclude_filter_regex_is_an_error,
        test_orphan_group_is_a_warning_not_an_error,
        test_proxy_group_is_allowlisted_as_orphan,
        test_unused_provider_is_a_warning,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All mihomo lint contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 .github/scripts/test-mihomo-lint.py`
Expected: `ModuleNotFoundError: No module named 'mihomo_lint'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/scripts/mihomo_lint.py`:

```python
#!/usr/bin/env python3
"""Referential-integrity checks for a mihomo config.

`mihomo -t` accepts a config whose rules point at groups that do not exist, so
this covers the class it misses: dangling references. Written against the real
config, which contains three shapes that break naive parsers - logic rules with
nested parentheses, `no-resolve` trailing modifiers, and rule targets that are
proxies (`DNS-OUT`) rather than groups.
"""

from __future__ import annotations

import re
from typing import Any

# Split on commas that are NOT inside parentheses, so a logic rule such as
# AND,((RULE-SET,x),(NETWORK,udp)),GROUP keeps its middle field intact.
_TOP_LEVEL_COMMA = re.compile(r",(?![^(]*\))")

# Matches RULE-SET references anywhere, including nested inside logic rules.
_RULESET_REFERENCE = re.compile(r"RULE-SET,([^,()]+)")

# Trailing flags that follow the target rather than being one.
_TARGET_MODIFIERS = {"no-resolve", "src", "dst"}

_BUILTIN_TARGETS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}

# Remnawave surfaces a group named PROXY that no rule targets. Not a defect.
_ALLOWED_ORPHAN_GROUPS = {"PROXY"}

_REGEX_FIELDS = ("filter", "exclude-filter", "exclude-type")


def rule_target(rule: str) -> str:
    """Return the outbound a rule dispatches to."""
    parts = [part.strip() for part in _TOP_LEVEL_COMMA.split(rule)]
    target = parts[-1]
    if target in _TARGET_MODIFIERS and len(parts) >= 2:
        target = parts[-2]
    return target


def lint_config(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a parsed mihomo config."""
    errors: list[str] = []
    warnings: list[str] = []

    groups = {group["name"] for group in config.get("proxy-groups") or []}
    proxies = {proxy["name"] for proxy in config.get("proxies") or []}
    providers = set(config.get("rule-providers") or {})
    rules = list(config.get("rules") or [])

    valid_targets = groups | proxies | _BUILTIN_TARGETS

    referenced_providers: set[str] = set()
    targets: set[str] = set()
    for rule in rules:
        referenced_providers.update(_RULESET_REFERENCE.findall(rule))
        target = rule_target(rule)
        targets.add(target)
        if target not in valid_targets:
            errors.append(f"rule targets unknown outbound {target!r}: {rule}")

    for missing in sorted(referenced_providers - providers):
        errors.append(f"rule references undeclared rule-provider {missing!r}")

    for unused in sorted(providers - referenced_providers):
        warnings.append(f"rule-provider {unused!r} is declared but never referenced")

    members: set[str] = set()
    for group in config.get("proxy-groups") or []:
        for member in group.get("proxies") or []:
            members.add(member)
            if member not in valid_targets:
                errors.append(f"group {group['name']!r} lists unknown member {member!r}")
        for field in _REGEX_FIELDS:
            pattern = group.get(field)
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"group {group['name']!r} has an invalid {field}: {exc}")

    for orphan in sorted(groups - targets - members - _ALLOWED_ORPHAN_GROUPS):
        warnings.append(f"group {orphan!r} is neither a rule target nor a member of any group")

    return errors, warnings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 .github/scripts/test-mihomo-lint.py`
Expected: eleven `PASS:` lines and `All mihomo lint contract tests passed.`

- [ ] **Step 5: Verify the linter is clean against the real config**

This is the acceptance check that matters: the gate must start green, otherwise a first red run cannot be told apart from a real breakage.

```bash
python3 -c "
import sys, yaml
sys.path.insert(0, '.github/scripts')
import mihomo_lint
config = yaml.safe_load(open('config/mihomo/default.template.yaml'))
errors, warnings = mihomo_lint.lint_config(config)
print('errors:', errors or 'none')
print('warnings:', warnings or 'none')
raise SystemExit(1 if errors else 0)
"
```

Expected: `errors: none`, exit code 0. Warnings may be non-empty and are not fatal.

- [ ] **Step 6: Commit**

```bash
git add .github/scripts/mihomo_lint.py .github/scripts/test-mihomo-lint.py
git commit -m "feat(mihomo): add referential-integrity linter

Covers what mihomo -t misses: rules pointing at groups that do not exist and
RULE-SETs naming undeclared providers. Handles the three shapes in the real
config that break naive parsing - nested-paren logic rules, no-resolve
modifiers, and proxy (not group) targets such as DNS-OUT."
```

---

## Task 5: Routing log parser

The parser is pure and gets its own tests; the Docker driver in Task 6 does not, because there is nothing in it worth asserting that the integration run does not already prove.

**Files:**
- Create: `.github/scripts/mihomo_routing.py`
- Test: `.github/scripts/test-mihomo-routing.py`

- [ ] **Step 1: Write the failing test**

Create `.github/scripts/test-mihomo-routing.py`:

```python
#!/usr/bin/env python3
"""Contract tests for mihomo_routing.py."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_routing  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# Verbatim lines captured from a real harness run on 2026-08-21.
REAL_LOG = '''\
time="2026-08-21T10:11:45.800262511Z" level=info msg="[TCP] 172.56.198.41:30089 --> www.youtube.com:443 match RuleSet(youtube) using 📺 YouTube[🇳🇱 Netherlands-2]"
time="2026-08-21T10:11:47.131630053Z" level=info msg="[TCP] 172.56.198.41:40549 --> chatgpt.com:443 match RuleSet(openai-inline) using 🤖 ChatGPT и AI[🇺🇸 USA-2]"
time="2026-08-21T10:11:52.796671792Z" level=info msg="[TCP] 172.56.198.41:43576 --> yandex.ru:443 match RuleSet(ru-inline) using ⚪🔵🔴 RU сайты[🇷🇺 Без VPN]"
time="2026-08-21T10:10:45.496878761Z" level=info msg="[TCP] mihomo --> raw.githubusercontent.com:443 using 🚫 Недоступные из РФ[🇷🇺 MSK YouTube Inst TG]"
time="2026-08-21T10:10:50.168255096Z" level=debug msg="Health Checked, proxy: DNS-OUT, url: https://www.gstatic.com/generate_204, alive: false"
'''


def test_parses_domain_rule_and_group() -> None:
    routes = mihomo_routing.parse_routes(REAL_LOG)
    assert_true(routes["www.youtube.com"].group == "📺 YouTube", f"group wrong: {routes['www.youtube.com']}")
    assert_true(routes["www.youtube.com"].rule == "RuleSet(youtube)", "rule wrong")
    assert_true(routes["www.youtube.com"].node == "🇳🇱 Netherlands-2", "node wrong")


def test_parses_group_names_containing_spaces_and_emoji() -> None:
    routes = mihomo_routing.parse_routes(REAL_LOG)
    assert_true(routes["chatgpt.com"].group == "🤖 ChatGPT и AI", f"multiword group wrong: {routes['chatgpt.com']}")
    assert_true(routes["yandex.ru"].group == "⚪🔵🔴 RU сайты", f"emoji group wrong: {routes['yandex.ru']}")


def test_ignores_internal_connections_without_a_match() -> None:
    # mihomo's own rule-provider downloads log a "using" line with no "match".
    # Counting them would attribute routes to domains nobody asked about.
    routes = mihomo_routing.parse_routes(REAL_LOG)
    assert_true("raw.githubusercontent.com" not in routes, "internal connection was treated as a route")


def test_ignores_non_connection_lines() -> None:
    routes = mihomo_routing.parse_routes(REAL_LOG)
    assert_true(len(routes) == 3, f"expected 3 routes, got {sorted(routes)}")


def test_parses_direct_outbound_without_node_brackets() -> None:
    line = 'time="x" level=info msg="[TCP] 10.0.0.1:5 --> example.com:443 match Match() using DIRECT"'
    routes = mihomo_routing.parse_routes(line)
    assert_true(routes["example.com"].group == "DIRECT", f"DIRECT not parsed: {routes}")
    assert_true(routes["example.com"].node == "", "DIRECT has no node and must not invent one")


def test_last_observation_wins() -> None:
    lines = (
        'time="x" level=info msg="[TCP] 1.1.1.1:1 --> a.com:443 match RuleSet(x) using 🅰 One[n1]"\n'
        'time="y" level=info msg="[TCP] 1.1.1.1:2 --> a.com:443 match RuleSet(x) using 🅱 Two[n2]"\n'
    )
    routes = mihomo_routing.parse_routes(lines)
    assert_true(routes["a.com"].group == "🅱 Two", f"later line should win: {routes['a.com']}")


def test_compare_routes_reports_missing_domain() -> None:
    expectations = [{"domain": "a.com", "group": "🅰 One"}]
    mismatches = mihomo_routing.compare_routes({}, expectations)
    assert_true(any("a.com" in m and "no route" in m for m in mismatches), f"missing domain not reported: {mismatches}")


def test_compare_routes_reports_wrong_group() -> None:
    actual = {"a.com": mihomo_routing.Route(rule="RuleSet(x)", group="🅱 Two", node="n2")}
    expectations = [{"domain": "a.com", "group": "🅰 One"}]
    mismatches = mihomo_routing.compare_routes(actual, expectations)
    assert_true(any("🅰 One" in m and "🅱 Two" in m for m in mismatches), f"wrong group not reported: {mismatches}")


def test_compare_routes_passes_when_matching() -> None:
    actual = {"a.com": mihomo_routing.Route(rule="RuleSet(x)", group="🅰 One", node="n1")}
    expectations = [{"domain": "a.com", "group": "🅰 One"}]
    assert_true(mihomo_routing.compare_routes(actual, expectations) == [], "matching routes must not report mismatches")


def main() -> int:
    tests = [
        test_parses_domain_rule_and_group,
        test_parses_group_names_containing_spaces_and_emoji,
        test_ignores_internal_connections_without_a_match,
        test_ignores_non_connection_lines,
        test_parses_direct_outbound_without_node_brackets,
        test_last_observation_wins,
        test_compare_routes_reports_missing_domain,
        test_compare_routes_reports_wrong_group,
        test_compare_routes_passes_when_matching,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All mihomo routing contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 .github/scripts/test-mihomo-routing.py`
Expected: `ModuleNotFoundError: No module named 'mihomo_routing'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/scripts/mihomo_routing.py` with the parser only (the driver arrives in Task 6):

```python
#!/usr/bin/env python3
"""Observe and compare mihomo routing decisions.

mihomo logs, at debug level, exactly what the gate needs:

    [TCP] 172.56.198.41:30089 --> www.youtube.com:443 match RuleSet(youtube) using 📺 YouTube[🇳🇱 Netherlands-2]

domain -> matched rule -> group -> concrete node. Assertions are built on the
rule match rather than on the HTTP response, because third-party anti-bot makes
responses unreliable: chatgpt.com answers 403 through a US node while routing
perfectly correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# docker logs wraps the payload in msg="...". Everything of interest is inside.
_MESSAGE = re.compile(r'msg="(?P<message>.*)"\s*$')

# The "match" keyword is required: mihomo's own rule-provider downloads emit a
# "using" line with no "match", and they are not routes anyone asked for.
_CONNECTION = re.compile(
    r"\[(?:TCP|UDP)\]\s+\S+\s+-->\s+(?P<host>[^\s:]+):\d+\s+"
    r"match\s+(?P<rule>\S+)\s+using\s+(?P<chain>.+?)\s*$"
)


@dataclass(frozen=True)
class Route:
    rule: str
    group: str
    node: str


def _split_chain(chain: str) -> tuple[str, str]:
    """Split "📺 YouTube[🇳🇱 Netherlands-2]" into group and node.

    Bare outbounds such as DIRECT carry no bracketed node.
    """
    chain = chain.strip()
    if chain.endswith("]") and "[" in chain:
        group, _, node = chain.rpartition("[")
        return group.strip(), node[:-1].strip()
    return chain, ""


def parse_routes(log_text: str) -> dict[str, Route]:
    """Extract domain -> Route from mihomo debug output."""
    routes: dict[str, Route] = {}
    for line in log_text.splitlines():
        message_match = _MESSAGE.search(line)
        message = message_match.group("message") if message_match else line
        connection = _CONNECTION.search(message)
        if not connection:
            continue
        group, node = _split_chain(connection.group("chain"))
        routes[connection.group("host")] = Route(
            rule=connection.group("rule"), group=group, node=node
        )
    return routes


def compare_routes(
    actual: dict[str, Route], expectations: Iterable[dict[str, Any]]
) -> list[str]:
    """Return human-readable mismatches against the expectations file."""
    mismatches: list[str] = []
    for expectation in expectations:
        domain = expectation["domain"]
        expected_group = expectation["group"]
        route = actual.get(domain)
        if route is None:
            mismatches.append(f"{domain}: no route observed (expected {expected_group!r})")
            continue
        if route.group != expected_group:
            mismatches.append(
                f"{domain}: expected {expected_group!r}, got {route.group!r} via {route.rule}"
            )
    return mismatches
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 .github/scripts/test-mihomo-routing.py`
Expected: nine `PASS:` lines and `All mihomo routing contract tests passed.`

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/mihomo_routing.py .github/scripts/test-mihomo-routing.py
git commit -m "feat(mihomo): parse routing decisions from mihomo debug logs

Asserts on the rule match rather than the HTTP response: chatgpt.com answers
403 through a US node while routing correctly, so a status-code gate would go
red for reasons unrelated to the config."
```

---

## Task 6: Docker harness driver

**Files:**
- Modify: `.github/scripts/mihomo_routing.py`

- [ ] **Step 1: Extend the module's imports**

Replace the import block at the top of `.github/scripts/mihomo_routing.py` (do not append these lower down — imports below code are valid Python but break flake8 E402 and hide the module's dependencies from a reader):

```python
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
```

- [ ] **Step 2: Add the driver**

Append to the end of `.github/scripts/mihomo_routing.py`:

```python
MIHOMO_IMAGE = "metacubex/mihomo:latest"
_CONTAINER_NAME = "mihomo-gate-harness"
_PROXY_PORT = 17890
_READY_TIMEOUT_SECONDS = 60


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)


def _wait_until_ready(secret: str, timeout: int = _READY_TIMEOUT_SECONDS) -> None:
    """Poll the Clash API until mihomo answers, or give up loudly."""
    request = urllib.request.Request(
        "http://127.0.0.1:19099/version", headers={"Authorization": f"Bearer {secret}"}
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                json.loads(response.read().decode("utf-8"))
                return
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(1)
    raise RuntimeError(f"mihomo did not become ready within {timeout}s")


def probe_routes(
    candidate: dict[str, Any], domains: list[str], *, secret: str
) -> dict[str, Route]:
    """Run the candidate in Docker, drive `domains` through it, return routes."""
    workdir = tempfile.mkdtemp(prefix="mihomo-gate-")
    config_path = Path(workdir) / "config.yaml"
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(candidate, handle, allow_unicode=True, sort_keys=False)

    _run(["docker", "rm", "-f", _CONTAINER_NAME])
    try:
        started = _run([
            "docker", "run", "-d", "--name", _CONTAINER_NAME,
            "-p", f"{_PROXY_PORT}:7890", "-p", "19099:9099",
            "-v", f"{workdir}:/cfg", MIHOMO_IMAGE, "-d", "/cfg",
        ])
        if started.returncode != 0:
            raise RuntimeError(f"failed to start mihomo: {started.stderr.strip()}")

        _wait_until_ready(secret)

        for domain in domains:
            _run([
                "curl", "--silent", "--output", "/dev/null", "--max-time", "20",
                "--proxy", f"http://127.0.0.1:{_PROXY_PORT}", f"https://{domain}",
            ])

        logs = _run(["docker", "logs", _CONTAINER_NAME])
        return parse_routes(logs.stdout + logs.stderr)
    finally:
        _run(["docker", "rm", "-f", _CONTAINER_NAME])


def config_test(candidate: dict[str, Any]) -> tuple[bool, str]:
    """Run `mihomo -t` against the candidate. Returns (ok, output)."""
    workdir = tempfile.mkdtemp(prefix="mihomo-test-")
    config_path = Path(workdir) / "config.yaml"
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(candidate, handle, allow_unicode=True, sort_keys=False)
    result = _run([
        "docker", "run", "--rm", "-v", f"{workdir}:/cfg", MIHOMO_IMAGE, "-d", "/cfg", "-t",
    ])
    output = result.stdout + result.stderr
    return result.returncode == 0, output
```

- [ ] **Step 3: Verify the parser tests still pass**

The new imports must not break the pure tests. `import yaml` at module top means the parser now needs PyYAML present — it already is, via `requirements.txt`.

Run: `python3 .github/scripts/test-mihomo-routing.py`
Expected: nine `PASS:` lines.

- [ ] **Step 4: Prove `config_test()` works against the real template**

No credentials and no real traffic — but the stand-in proxy list must include the template's OWN `proxies:` entries.

That stub is not empty. It holds two static proxies: `🇷🇺 Без VPN` (type `direct`) and `DNS-OUT` (type `dns`). Three groups list `🇷🇺 Без VPN` as a member, and the config's very first rule is `DST-PORT,53,DNS-OUT`. The panel preserves both and appends the subscriber's real nodes on top — confirmed against a live rendered subscription, which returns exactly those two plus seven VLESS nodes. So `build_candidate()` replacing `proxies:` wholesale is right for real use; a stand-in list that drops the statics is simply unrepresentative.

```bash
python3 -c "
import sys, yaml
sys.path.insert(0, '.github/scripts')
import mihomo_candidate, mihomo_routing
template = yaml.safe_load(open('config/mihomo/default.template.yaml'))
stand_in = [{'name': 'probe', 'type': 'direct', 'udp': True}]
candidate = mihomo_candidate.build_candidate(template, template['proxies'] + stand_in)
ok, output = mihomo_routing.config_test(candidate)
print('mihomo -t ok:', ok)
print(output.strip()[-400:])
raise SystemExit(0 if ok else 1)
"
```

Expected: `mihomo -t ok: True`, output ending in `configuration file /cfg/config.yaml test is successful`. Several `provider is Classical` warnings are normal.

**This is the clearest evidence for why layer 2 exists.** Dropping the statics was tried during implementation. `mihomo -t` reported ONE missing reference — and a *different* one on each run, because Go randomises map iteration order, so the error names whichever group it evaluated first. `mihomo_lint.lint_config()` on the same input reported all four problems, deterministically and by name:

```
rule targets unknown outbound 'DNS-OUT': DST-PORT,53,DNS-OUT
group '🧲 Торрент-трекеры' lists unknown member '🇷🇺 Без VPN'
group '🎮 Игры' lists unknown member '🇷🇺 Без VPN'
group '⚪🔵🔴 RU сайты' lists unknown member '🇷🇺 Без VPN'
```

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/mihomo_routing.py
git commit -m "feat(mihomo): drive the candidate config in a Docker harness"
```

---

## Task 7: Routing expectations golden file

**Files:**
- Create: `config/mihomo/routing-expectations.yaml`

- [ ] **Step 1: Seed the file from an observed run**

The expectations must describe reality at capture time, otherwise the first gate run is red for reasons unrelated to any change. Generate it, then read it before committing.

A subscription URL is a bearer credential — anyone holding it gets working proxy access. Keep it out of this file, out of shell history and out of commits. Put it in `~/.mihomo_test_subscription_url` (mode `600`) and read it from there:

```bash
# One-time, and never in a file that gets committed:
#   printf 'https://no.watchd0g.dev/<token>\n' > ~/.mihomo_test_subscription_url
#   chmod 600 ~/.mihomo_test_subscription_url
export MIHOMO_TEST_SUBSCRIPTION_URL="$(tr -d '\n' < ~/.mihomo_test_subscription_url)"
python3 - <<'PY'
import os, sys, yaml
sys.path.insert(0, '.github/scripts')
import mihomo_panel_api, mihomo_candidate, mihomo_routing

DOMAINS = [
    "www.youtube.com", "chatgpt.com", "claude.ai", "x.com", "instagram.com",
    "discord.com", "api.telegram.org", "soundcloud.com", "yandex.ru",
    "www.gstatic.com", "ru.watchd0g.dev",
]

template = yaml.safe_load(open('config/mihomo/default.template.yaml'))
proxies = mihomo_panel_api.fetch_rendered_proxies(
    os.environ['MIHOMO_TEST_SUBSCRIPTION_URL']
)
candidate = mihomo_candidate.build_candidate(template, proxies)
routes = mihomo_routing.probe_routes(
    candidate, DOMAINS, secret=mihomo_candidate.HARNESS_SECRET
)

document = {
    'expectations': [
        {'domain': d, 'group': routes[d].group}
        for d in DOMAINS if d in routes
    ]
}
missing = [d for d in DOMAINS if d not in routes]
with open('config/mihomo/routing-expectations.yaml', 'w', encoding='utf-8') as handle:
    handle.write(
        "# Golden file: where each domain must route.\n"
        "# Changing a route means changing this file in the same commit - the gate\n"
        "# fails on any deviation, which is how an intended change is told apart\n"
        "# from a regression. Seeded from an observed run on the captured template.\n"
    )
    yaml.safe_dump(document, handle, allow_unicode=True, sort_keys=False)
print('observed:', len(document['expectations']))
print('NO ROUTE OBSERVED (investigate before committing):', missing or 'none')
PY
```

Expected: `observed: 11` and `NO ROUTE OBSERVED ... none`. Any domain in the missing list either failed to connect or is unreachable from the runner — remove it from `DOMAINS` and note why, rather than committing an expectation that cannot be checked.

- [ ] **Step 2: Read the generated file and sanity-check it**

Run: `cat config/mihomo/routing-expectations.yaml`

Check by eye that the groups are the intended ones — in particular that `www.youtube.com` maps to `📺 YouTube` and `yandex.ru` maps to `⚪🔵🔴 RU сайты`. A seeded golden file locks in whatever is true today, including a bug, so this read is the only thing standing between a mistake and it becoming "expected".

- [ ] **Step 3: Commit**

```bash
git add config/mihomo/routing-expectations.yaml
git commit -m "feat(mihomo): add routing expectations golden file

Seeded from an observed harness run against the captured template, so the gate
starts green and a first red run means a real change."
```

---

## Task 8: Gate orchestrator

**Files:**
- Create: `.github/scripts/mihomo_gate.py`

- [ ] **Step 1: Write the orchestrator**

Create `.github/scripts/mihomo_gate.py`:

```python
#!/usr/bin/env python3
"""Run every validation layer against the repo's mihomo config.

Layers, in order of how cheap they are to fail:
  1. mihomo -t                 - syntax and schema
  2. referential integrity     - dangling rules, providers, filters
  3. routing probe             - what actually happens to each domain
  4. regression comparison     - does that match the checked-in expectations

Phase A only. Nothing here patches the panel or touches a router.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import mihomo_candidate  # noqa: E402
import mihomo_lint  # noqa: E402
import mihomo_panel_api  # noqa: E402
import mihomo_routing  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_TEMPLATE = REPO_ROOT / "config" / "mihomo" / "default.template.yaml"
DEFAULT_EXPECTATIONS = REPO_ROOT / "config" / "mihomo" / "routing-expectations.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the mihomo routing config.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--report", type=Path, default=Path("mihomo-gate-report.md"))
    args = parser.parse_args()

    subscription_url = os.environ.get("MIHOMO_TEST_SUBSCRIPTION_URL", "").strip()
    if not subscription_url:
        print("MIHOMO_TEST_SUBSCRIPTION_URL is required.", file=sys.stderr)
        return 2

    template = yaml.safe_load(args.template.read_text(encoding="utf-8"))
    expectations = yaml.safe_load(args.expectations.read_text(encoding="utf-8"))["expectations"]
    domains = [item["domain"] for item in expectations]

    # Fetched via curl inside mihomo_panel_api: the macOS Python.framework
    # interpreters have no CA bundle and urllib fails against the panel host.
    # The response carries live credentials - never log it or upload it.
    proxies = mihomo_panel_api.fetch_rendered_proxies(subscription_url)
    candidate = mihomo_candidate.build_candidate(template, proxies)

    report: list[str] = ["## mihomo config gate", ""]
    failed = False

    ok, output = mihomo_routing.config_test(candidate)
    report.append(f"- **Syntax (`mihomo -t`)**: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failed = True
        report.extend(["", "```", output.strip()[-2000:], "```", ""])

    errors, warnings = mihomo_lint.lint_config(candidate)
    report.append(f"- **Referential integrity**: {'PASS' if not errors else 'FAIL'}")
    for error in errors:
        failed = True
        report.append(f"  - error: {error}")
    for warning in warnings:
        report.append(f"  - warning: {warning}")

    if failed:
        # A config that does not parse cannot be routed through; stop here
        # rather than emitting a confusing pile of "no route observed".
        report.append("")
        report.append("Routing probe skipped: fix the failures above first.")
        args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n".join(report))
        return 1

    routes = mihomo_routing.probe_routes(
        candidate, domains, secret=mihomo_candidate.HARNESS_SECRET
    )
    mismatches = mihomo_routing.compare_routes(routes, expectations)
    report.append(f"- **Routing regression**: {'PASS' if not mismatches else 'FAIL'}")

    report.extend(["", "| domain | rule | group | node |", "|---|---|---|---|"])
    for item in expectations:
        route = routes.get(item["domain"])
        if route is None:
            report.append(f"| {item['domain']} | — | **no route** | — |")
            continue
        marker = "" if route.group == item["group"] else " ⚠️"
        report.append(
            f"| {item['domain']} | `{route.rule}` | {route.group}{marker} | {route.node} |"
        )

    if mismatches:
        failed = True
        report.extend(["", "**Mismatches against `routing-expectations.yaml`:**", ""])
        report.extend(f"- {mismatch}" for mismatch in mismatches)
        report.extend([
            "",
            "If this change is intentional, update `config/mihomo/routing-expectations.yaml`",
            "in the same commit so the diff records the intent.",
        ])

    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the gate locally against the unmodified config**

```bash
export MIHOMO_TEST_SUBSCRIPTION_URL="$(tr -d '\n' < ~/.mihomo_test_subscription_url)"
python3 .github/scripts/mihomo_gate.py
echo "exit=$?"
```

Expected: all three layers `PASS`, a filled-in routing table, `exit=0`.

- [ ] **Step 3: Prove the gate actually catches a regression**

A gate that has never gone red is not known to work. Break a route deliberately, confirm the failure, then restore.

```bash
cp config/mihomo/default.template.yaml /tmp/template.backup
python3 - <<'PY'
text = open('config/mihomo/default.template.yaml', encoding='utf-8').read()
broken = text.replace('RULE-SET,youtube,📺 YouTube', 'RULE-SET,youtube,⚪🔵🔴 RU сайты')
assert broken != text, 'the youtube rule was not found - inspect the file'
open('config/mihomo/default.template.yaml', 'w', encoding='utf-8').write(broken)
print('youtube repointed to the RU group')
PY
python3 .github/scripts/mihomo_gate.py; echo "exit=$?"
cp /tmp/template.backup config/mihomo/default.template.yaml
```

Expected: `Routing regression: FAIL`, a mismatch line naming `www.youtube.com`, `exit=1`. Then the file is restored.

- [ ] **Step 4: Confirm the restore was clean**

Run: `git diff --stat config/mihomo/default.template.yaml`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/mihomo_gate.py
git commit -m "feat(mihomo): add config gate orchestrator

Runs syntax, integrity, routing and regression layers and writes a Markdown
report. Verified to go red when the youtube rule is repointed at the RU group."
```

---

## Task 9: CI wiring

**Files:**
- Modify: `.github/workflows/ansible-ci.yml`
- Create: `.github/workflows/mihomo-config-gate.yml`

- [ ] **Step 1: Add the contract tests to ansible-ci**

In `.github/workflows/ansible-ci.yml`, after the existing `OpenWrt fleet renderer contract tests` step, add:

```yaml
      - name: mihomo candidate contract tests
        run: python .github/scripts/test-mihomo-candidate.py

      - name: mihomo lint contract tests
        run: python .github/scripts/test-mihomo-lint.py

      - name: mihomo routing contract tests
        run: python .github/scripts/test-mihomo-routing.py
```

These need no Docker and no secrets, so they belong in the existing fast job.

- [ ] **Step 2: Verify the contract tests pass locally**

```bash
python3 .github/scripts/test-mihomo-candidate.py
python3 .github/scripts/test-mihomo-lint.py
python3 .github/scripts/test-mihomo-routing.py
```

Expected: three summary lines, all passing.

- [ ] **Step 3: Create the PR gate workflow**

Create `.github/workflows/mihomo-config-gate.yml`:

```yaml
---
name: mihomo-config-gate

# Validates the routing config on every PR that touches it. Phase A: checks
# only - the panel is not patched and no router is touched.
#
# The gate needs real proxies to build a runnable candidate, so it reads a
# rendered subscription at run time. That response carries live credentials and
# must never be written to a log or an artifact.
on:
  pull_request:
    paths:
      - "config/mihomo/**"
      - ".github/scripts/mihomo_*.py"
      - ".github/workflows/mihomo-config-gate.yml"
  workflow_dispatch:

jobs:
  gate:
    runs-on: ubuntu-latest
    environment: Testing
    permissions:
      contents: read
      pull-requests: write
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install PyYAML

      - name: Pull the mihomo image
        run: docker pull metacubex/mihomo:latest

      - name: Run the config gate
        env:
          MIHOMO_TEST_SUBSCRIPTION_URL: ${{ secrets.MIHOMO_TEST_SUBSCRIPTION_URL }}
        run: python .github/scripts/mihomo_gate.py --report mihomo-gate-report.md

      - name: Comment the routing report on the PR
        if: always() && github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            if (!fs.existsSync('mihomo-gate-report.md')) return;
            const body = fs.readFileSync('mihomo-gate-report.md', 'utf8');
            await github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body,
            });
```

- [ ] **Step 4: Verify workflow YAML lints**

Run: `yamllint .github/workflows/mihomo-config-gate.yml`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ansible-ci.yml .github/workflows/mihomo-config-gate.yml
git commit -m "ci(mihomo): gate config changes on PRs

Contract tests join the existing fast job; the Docker-backed gate runs only on
PRs that touch the config and posts its routing table as a comment."
```

---

## Post-merge verification

- [ ] Add the repository secret `MIHOMO_TEST_SUBSCRIPTION_URL` (environment `Testing`), value `https://no.watchd0g.dev/<a token>`. Prefer a dedicated canary subscriber over reusing a router's token, so revoking it later cannot break a client.
- [ ] Open a throwaway PR that repoints one rule, confirm the gate goes red and comments the mismatch, then close it without merging.
- [ ] Confirm `ansible-ci` is green on master.
- [ ] `bd close as-5g2` is **not** appropriate yet — Phase A closes only the gate half. Record progress on the epic and open the Phase B plan.

---

## Deliberately not in this plan

- Any PATCH to the panel.
- Any contact with a router, including the canary.
- Auto-rollback.
- Removing `mihomo-remnawave-davoyan-rubypass-v2.*.yaml`. They are superseded once Task 1 lands, but deleting them is a separate, reversible decision and mixing it into the capture commit would obscure the capture's diff.
