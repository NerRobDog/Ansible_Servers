#!/usr/bin/env python3
"""
Anti-regression tests for mihomo config.

Two test modes:
  1. STATIC  — parse YAML, verify rule routing logic and proxy group composition
  2. LIVE    — fire real HTTP requests through the proxy and check responses

Usage:
  python3 test_mihomo_config.py                        # static only
  python3 test_mihomo_config.py --live                 # static + live (needs mihomo running)
  python3 test_mihomo_config.py --live --proxy 7897    # custom port (default 7897)
  python3 test_mihomo_config.py --config path/to.yaml  # custom config file
"""

import sys, os, re, yaml, argparse, urllib.request, subprocess, json, time
from pathlib import Path

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
DEFAULT_CONFIG = Path(__file__).parent / "mihomo-remnawave-davoyan-rubypass-v2.2.yaml"
PROXY_PORT = 7897
API_PORT   = 9091

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⏭  SKIP"

errors = []
warnings = []


def ok(msg):   print(f"  {PASS}  {msg}")
def fail(msg): print(f"  {FAIL}  {msg}"); errors.append(msg)
def warn(msg): print(f"  ⚠️  WARN  {msg}"); warnings.append(msg)
def skip(msg): print(f"  {SKIP}  {msg}")
def section(title): print(f"\n{'─'*55}\n  {title}\n{'─'*55}")


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)

def proxy_groups_dict(cfg):
    return {g['name']: g for g in cfg.get('proxy-groups', [])}

def rule_providers_dict(cfg):
    return cfg.get('rule-providers', {})

def rules_list(cfg):
    return cfg.get('rules', [])

def first_matching_rule(domain, rules_raw):
    """Very simplified rule matching (domain-suffix / keyword / match only)."""
    for rule in rules_raw:
        parts = rule.split(',')
        if len(parts) < 2:
            continue
        rtype = parts[0].strip()
        if rtype == 'MATCH':
            return rule, parts[-1].strip()
        if rtype in ('RULE-SET',):
            # Can't resolve rule-set contents here statically — skip
            continue
        if rtype == 'DOMAIN-SUFFIX':
            if domain.endswith(parts[1].strip()) or domain == parts[1].strip():
                return rule, parts[-1].strip()
        if rtype == 'DOMAIN-KEYWORD':
            if parts[1].strip().lower() in domain.lower():
                return rule, parts[-1].strip()
        if rtype == 'DOMAIN':
            if domain == parts[1].strip():
                return rule, parts[-1].strip()
    return None, None

def rule_set_index(rules_raw, rule_set_name):
    """Return the index of a RULE-SET rule in the rules list."""
    for i, r in enumerate(rules_raw):
        if r.startswith(f"RULE-SET,{rule_set_name},"):
            return i
    return -1

def http_get(url, proxy=None, timeout=15):
    """Use curl for HTTP requests — avoids Python SSL cert issues on macOS."""
    cmd = [
        "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
        "-L", "--max-time", str(timeout), "--connect-timeout", "10",
        "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    ]
    if proxy:
        cmd += ["--proxy", f"http://127.0.0.1:{proxy}"]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        code_str = result.stdout.strip()
        code = int(code_str) if code_str.isdigit() else 0
        return code, (result.stderr.strip() if code == 0 else None)
    except subprocess.TimeoutExpired:
        return 0, "timeout"
    except Exception as e:
        return 0, str(e)

def mihomo_api(path, method="GET", body=None):
    url = f"http://127.0.0.1:{API_PORT}{path}"
    cmd = ["curl", "-s", "-X", method, "-w", "\n%{http_code}",
           "-H", "Content-Type: application/json", url]
    if body:
        cmd += ["-d", json.dumps(body)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().rsplit("\n", 1)
        body_text = lines[0] if len(lines) > 1 else ""
        code = int(lines[-1]) if lines[-1].isdigit() else 0
        if code == 204 or not body_text:
            return {}
        return json.loads(body_text)
    except Exception as e:
        return None

def set_proxy_group(group, proxy_name):
    return mihomo_api(f"/proxies/{urllib.request.quote(group)}", "PUT", {"name": proxy_name})


# ──────────────────────────────────────────────
# STATIC TESTS
# ──────────────────────────────────────────────
def test_yaml_valid(cfg):
    section("1. YAML validity")
    if cfg:
        ok("Config parses as valid YAML")
    else:
        fail("Config is empty or invalid")

def test_version_comment(cfg_path):
    section("2. Version comment")
    with open(cfg_path) as f:
        first = f.read(300)
    m = re.search(r'Version:\s*(v[\d.]+)', first)
    if m:
        ok(f"Version tag present: {m.group(1)}")
    else:
        warn("No version tag found in header comment")

def test_warp_excluded_from_load_balance(cfg):
    section("3. WatchNet/WARP excluded from 🌍 load-balance pool")
    groups = proxy_groups_dict(cfg)
    lb = groups.get("🌍 Зарубежные серверы (баланс)", {})
    excl = lb.get("exclude-filter", "")
    if re.search(r'WatchNet', excl, re.IGNORECASE):
        ok(f"exclude-filter contains 'WatchNet': {excl[:80]}")
    else:
        fail(
            "WatchNet is NOT excluded from 🌍 Зарубежные серверы (баланс)! "
            "Claude/ChatGPT may be routed through Cloudflare IPs → HTTP 403."
        )

def test_ru_nodes_excluded_from_load_balance(cfg):
    section("4. RU nodes excluded from 🌍 load-balance pool")
    groups = proxy_groups_dict(cfg)
    lb = groups.get("🌍 Зарубежные серверы (баланс)", {})
    excl = lb.get("exclude-filter", "")
    ru_patterns = ["🇷🇺", r"\bRU\b", "Russia", "Moscow", "SPB"]
    hits = [p for p in ru_patterns if re.search(p, excl)]
    if len(hits) >= 3:
        ok(f"RU patterns in exclude-filter: {hits}")
    else:
        fail(f"RU exclude-filter looks incomplete. Found: {hits}")

def test_rule_order_warp_before_ru_inside(cfg):
    section("5. google-warp-inline rule is BEFORE ru-inside")
    rules = rules_list(cfg)
    idx_warp   = rule_set_index(rules, "google-warp-inline")
    idx_inside = rule_set_index(rules, "ru-inside")
    if idx_warp == -1:
        fail("google-warp-inline rule not found")
    elif idx_inside == -1:
        fail("ru-inside rule not found")
    elif idx_warp < idx_inside:
        ok(f"google-warp-inline [{idx_warp}] < ru-inside [{idx_inside}]")
    else:
        fail(f"google-warp-inline [{idx_warp}] is AFTER ru-inside [{idx_inside}] — Google WARP services may hit RU nodes")

def test_rule_order_intl_blocked_before_ru_inside(cfg):
    section("6. intl-blocked-inline (Notion etc.) is BEFORE ru-inside")
    rules = rules_list(cfg)
    idx_intl   = rule_set_index(rules, "intl-blocked-inline")
    idx_inside = rule_set_index(rules, "ru-inside")
    if idx_intl == -1:
        fail("intl-blocked-inline rule not found")
    elif idx_intl < idx_inside:
        ok(f"intl-blocked-inline [{idx_intl}] < ru-inside [{idx_inside}]")
    else:
        fail(f"intl-blocked-inline [{idx_intl}] is AFTER ru-inside [{idx_inside}] — Notion may get RU IPs")

def test_rule_order_adult_before_ru_inside(cfg):
    section("7. adult rules are BEFORE ru-inside")
    rules = rules_list(cfg)
    idx_adult  = rule_set_index(rules, "adult")
    idx_inside = rule_set_index(rules, "ru-inside")
    if idx_adult == -1:
        fail("adult rule not found")
    elif idx_adult < idx_inside:
        ok(f"adult [{idx_adult}] < ru-inside [{idx_inside}]")
    else:
        fail(f"adult [{idx_adult}] is AFTER ru-inside [{idx_inside}] — pornhub/xvideos may hit slow RU nodes")

def test_rule_order_openai_before_ru_inside(cfg):
    section("8. openai-inline / ai rules are BEFORE ru-inside")
    rules = rules_list(cfg)
    idx_ai     = rule_set_index(rules, "ai")
    idx_openai = rule_set_index(rules, "openai-inline")
    idx_inside = rule_set_index(rules, "ru-inside")
    if idx_ai < idx_inside:
        ok(f"ai [{idx_ai}] < ru-inside [{idx_inside}]")
    else:
        fail(f"ai [{idx_ai}] is AFTER ru-inside [{idx_inside}]")
    if idx_openai < idx_inside:
        ok(f"openai-inline [{idx_openai}] < ru-inside [{idx_inside}]")
    else:
        fail(f"openai-inline [{idx_openai}] is AFTER ru-inside [{idx_inside}]")

def test_notion_in_intl_blocked(cfg):
    section("9. notion.so is in intl-blocked-inline")
    rp = rule_providers_dict(cfg)
    ibi = rp.get("intl-blocked-inline", {})
    payload = " ".join(ibi.get("payload", []))
    if "notion.so" in payload:
        ok("notion.so found in intl-blocked-inline")
    else:
        fail("notion.so NOT in intl-blocked-inline — Notion will hit RU bypass nodes")

def test_no_russia_hosts_provider(cfg):
    section("9b. no-russia-hosts rule-provider exists and is before ru-inside")
    rp = rule_providers_dict(cfg)
    nrh = rp.get("no-russia-hosts", {})
    if not nrh:
        fail("no-russia-hosts rule-provider not found — geo-banned sites (Autodesk etc.) hit RU nodes")
        return
    url = nrh.get("url", "")
    if "dartraiden" in url:
        ok(f"no-russia-hosts provider present: {url[:60]}")
    else:
        warn(f"no-russia-hosts url unexpected: {url}")
    rules = rules_list(cfg)
    idx_nrh    = rule_set_index(rules, "no-russia-hosts")
    idx_inside = rule_set_index(rules, "ru-inside")
    if idx_nrh == -1:
        fail("RULE-SET,no-russia-hosts not in rules list")
    elif idx_nrh < idx_inside:
        ok(f"no-russia-hosts [{idx_nrh}] < ru-inside [{idx_inside}] — geo-ban sites get foreign servers")
    else:
        fail(f"no-russia-hosts [{idx_nrh}] is AFTER ru-inside [{idx_inside}] — geo-ban sites hit RU-bypass")

def test_onlyfans_in_adult_extra(cfg):
    section("10. onlyfans.com is in adult-extra-inline")
    rp = rule_providers_dict(cfg)
    aei = rp.get("adult-extra-inline", {})
    payload = " ".join(aei.get("payload", []))
    if "onlyfans.com" in payload:
        ok("onlyfans.com found in adult-extra-inline")
    else:
        fail("onlyfans.com NOT in adult-extra-inline")

def test_google_warp_domains(cfg):
    section("11. Key Google services in google-warp-inline")
    rp = rule_providers_dict(cfg)
    gwi = rp.get("google-warp-inline", {})
    payload = " ".join(gwi.get("payload", []))
    required = ["notebooklm.google", "antigravity.google", "gemini.google.com", "aistudio.google.com"]
    for d in required:
        if d in payload:
            ok(f"{d} in google-warp-inline")
        else:
            fail(f"{d} NOT in google-warp-inline")

def test_warp_group_filter(cfg):
    section("12. 🏴‍☠️ WARP group filter targets WatchNet only")
    groups = proxy_groups_dict(cfg)
    warp = groups.get("🏴‍☠️ Cloudflare WARP", {})
    filt = warp.get("filter", "")
    if "WatchNet" in filt:
        ok(f"WARP group filter: {filt}")
    else:
        fail(f"WARP group filter doesn't mention WatchNet: {filt!r}")

def test_claude_not_in_warp_domains(cfg):
    section("13. claude.ai is NOT in google-warp-inline (would cause 403)")
    rp = rule_providers_dict(cfg)
    gwi = rp.get("google-warp-inline", {})
    payload = " ".join(gwi.get("payload", []))
    if "claude" not in payload.lower():
        ok("claude.ai not in google-warp-inline — correct")
    else:
        fail("claude.ai IS in google-warp-inline — Anthropic blocks Cloudflare IPs, will get 403")

def test_dns_mode(cfg):
    section("14. DNS mode is fake-ip")
    mode = cfg.get("dns", {}).get("enhanced-mode", "")
    if mode == "fake-ip":
        ok(f"DNS enhanced-mode: {mode}")
    else:
        warn(f"DNS mode is '{mode}', expected 'fake-ip'")

def test_mode_is_rule(cfg):
    section("15. Routing mode is 'rule'")
    mode = cfg.get("mode", "")
    if mode == "rule":
        ok("mode: rule")
    else:
        fail(f"mode is '{mode}', expected 'rule' — all routing logic is bypassed!")


# ─── v2.2 regression tests ─────────────────────────────────────

def test_perplexity_in_openai_inline(cfg):
    section("16. Perplexity domains in openai-inline (not relying on upstream ai.mrs)")
    rp = rule_providers_dict(cfg).get("openai-inline", {})
    payload = "\n".join(str(p) for p in rp.get("payload", []))
    for d in ("perplexity.ai", "pplx.ai"):
        if d in payload:
            ok(f"{d} found in openai-inline")
        else:
            fail(f"{d} missing from openai-inline — falls back to MetaCubeX ai.mrs (drift risk)")

def test_warp_excluded_from_ai_group(cfg):
    section("17. 🤖 ChatGPT и AI excludes WatchNet/WARP (Claude/ChatGPT/Perplexity 403 on CF)")
    g = proxy_groups_dict(cfg).get("🤖 ChatGPT и AI", {})
    exc = g.get("exclude-filter", "")
    if not g.get("include-all"):
        fail("🤖 ChatGPT и AI missing include-all — individual node picker disabled")
        return
    if "WatchNet" in exc:
        ok(f"WatchNet in AI exclude-filter: {exc}")
    else:
        fail(f"WatchNet NOT excluded from AI group ({exc}) — would route Claude/OpenAI via 403-blocked CF IPs")

def test_node_picker_enabled(cfg):
    section("18. include-all on Discord / Игры / ChatGPT (individual node picker)")
    groups = proxy_groups_dict(cfg)
    for name in ("💬 Discord", "🎮 Игры", "🤖 ChatGPT и AI"):
        g = groups.get(name, {})
        if g.get("include-all"):
            ok(f"{name}: include-all=true")
        else:
            fail(f"{name}: include-all missing — user cannot pin specific node")

def test_youtube_aliases(cfg):
    section("19. YouTube aliases exist with correct targets + hidden")
    groups = proxy_groups_dict(cfg)
    yt = groups.get("📺 YouTube", {})
    expected_aliases = ["📺 YT без рекламы", "📺 YT фон / PiP"]
    yt_proxies = yt.get("proxies", [])
    if yt_proxies == expected_aliases:
        ok(f"📺 YouTube exposes aliased names: {yt_proxies}")
    else:
        fail(f"📺 YouTube proxies = {yt_proxies}, expected {expected_aliases}")

    cases = [
        ("📺 YT без рекламы", "🚫 Недоступные из РФ"),
        ("📺 YT фон / PiP",  "🌍 Зарубежные серверы (баланс)"),
    ]
    for alias, target in cases:
        a = groups.get(alias)
        if not a:
            fail(f"alias group {alias} missing")
            continue
        if a.get("proxies") == [target]:
            ok(f"{alias} -> {target}")
        else:
            fail(f"{alias} -> {a.get('proxies')}, expected [{target}]")
        if a.get("hidden") is True:
            ok(f"{alias} hidden:true")
        else:
            fail(f"{alias} not hidden — clutters UI")

def test_hidden_internals(cfg):
    section("20. Background groups hidden from UI")
    groups = proxy_groups_dict(cfg)
    must_be_hidden = [
        "🌍 Зарубежные серверы (баланс)",
        "🇷🇺 Обход блокировок РФ (авто)",
        "PROXY",
    ]
    for name in must_be_hidden:
        g = groups.get(name, {})
        if g.get("hidden") is True:
            ok(f"{name}: hidden:true")
        else:
            fail(f"{name}: hidden flag missing — leaks into UI selector list")

def test_nudevista_routed_via_warp(cfg):
    section("22. nudevista routed via WARP (self-censors non-CF exit IPs with RU court stub)")
    rp = rule_providers_dict(cfg).get("google-warp-inline", {})
    payload = "\n".join(str(p) for p in rp.get("payload", []))
    if "nudevista" in payload:
        ok("nudevista in google-warp-inline → 🏴‍☠️ Cloudflare WARP")
    else:
        fail("nudevista missing from google-warp-inline — site shows RU court stub on US/non-CF exits")
    # Sanity: google-warp-inline must precede adult/ru-inside so WARP wins
    rules = rules_list(cfg)
    w = rule_set_index(rules, "google-warp-inline")
    a = rule_set_index(rules, "adult")
    r = rule_set_index(rules, "ru-inside")
    if w is not None and a is not None and w < a:
        ok(f"google-warp-inline [{w}] < adult [{a}]")
    else:
        fail(f"google-warp-inline [{w}] vs adult [{a}] — order broken")
    if w is not None and r is not None and w < r:
        ok(f"google-warp-inline [{w}] < ru-inside [{r}]")
    else:
        fail(f"google-warp-inline [{w}] vs ru-inside [{r}] — order broken")

def test_de_matrix_udp_bypass(cfg):
    section("23. de-matrix-1 (193.233.75.48/32) DIRECT bypass for UDP smoke tests")
    rules = rules_list(cfg)
    target = "IP-CIDR,193.233.75.48/32,DIRECT"
    idx = next((i for i, r in enumerate(rules) if target in r), None)
    if idx is None:
        fail(f"missing rule: {target}")
        return
    ok(f"rule present at [{idx}]: {rules[idx]}")
    # Must precede MATCH/proxy-routing rules — placed near top after private bypass
    priv = next((i for i, r in enumerate(rules) if "geosite-private" in r), None)
    if priv is not None and idx > priv:
        ok(f"de-matrix bypass [{idx}] > geosite-private [{priv}] (correct: after private)")
    # Must come before any proxy-routing rule
    first_proxy = next((i for i, r in enumerate(rules)
                        if any(g in r for g in ("📺", "➤", "💬", "🤖", "🎮", "🔞", "🚫"))), None)
    if first_proxy is not None and idx < first_proxy:
        ok(f"de-matrix bypass [{idx}] < first proxy rule [{first_proxy}]")
    else:
        fail(f"de-matrix bypass not early enough — idx={idx}, first proxy rule={first_proxy}")

def test_group_order_global_first(cfg):
    section("21. 🌍 Остальные сайты (Global) is first proxy-group")
    groups = cfg.get("proxy-groups", [])
    if not groups:
        fail("no proxy-groups")
        return
    first = groups[0].get("name")
    if first == "🌍 Остальные сайты":
        ok(f"first group: {first}")
    else:
        fail(f"first group is '{first}', expected '🌍 Остальные сайты' (Global)")


# ──────────────────────────────────────────────
# LIVE TESTS
# ──────────────────────────────────────────────
LIVE_TESTS = [
    # (label, url, expected_http_min, expected_http_max, proxy_group, proxy_name, notes)
    # proxy_group/proxy_name: switch mihomo GLOBAL to this before testing. None = don't switch.
    # NOTE: in mode:rule these switches only affect MATCH traffic; ruled traffic uses its group.
    #
    # For services where server IPs may be blocked by the provider (e.g. Anthropic blocks
    # datacenter IPs), the http check is replaced by a routing check — see ROUTING_TESTS below.
    ("notebooklm.google.com",    "https://notebooklm.google.com",       200, 399, None, None, "Needs WARP/Cloudflare IP"),
    ("antigravity.google",       "https://antigravity.google/",         200, 399, None, None, "Needs WARP/Cloudflare IP"),
    ("notion.so",                "https://www.notion.so",               200, 399, None, None, "Must NOT hit RU nodes — geo-ban"),
    ("youtube.com",              "https://www.youtube.com",             200, 399, None, None, "Should work via any non-RU node"),
    ("pornhub.com",              "https://www.pornhub.com",             200, 399, None, None, "Adult — should go through foreign"),
    ("xvideos.com",              "https://www.xvideos.com",             200, 399, None, None, "Adult — was going through RU nodes"),
    ("onlyfans.com",             "https://onlyfans.com",                200, 403, None, None, "403 is OnlyFans anti-bot, not geo-block"),
    ("telegram (web)",           "https://web.telegram.org",            200, 399, None, None, ""),
    ("discord.com",              "https://discord.com",                 200, 399, None, None, ""),
    ("yandex.ru (direct)",       "https://yandex.ru",                   200, 399, None, None, "RU site — DIRECT"),
]

# Routing tests: verify WHICH proxy group handles the request (via mihomo logs).
# This catches the real regression: wrong routing, regardless of whether server blocks the IP.
# Format: (label, url, expected_group_substring, must_not_contain, notes)
ROUTING_TESTS = [
    ("claude.ai routing",
     "https://claude.ai",
     "ChatGPT и AI",        # must match RuleSet(ai) → 🤖 ChatGPT и AI
     "WatchNet",            # must NOT go through WARP
     "WARP gives 403; non-WARP server IPs may also be blocked by Anthropic (infra issue, not config)"),

    ("chatgpt.com routing",
     "https://chatgpt.com",
     "ChatGPT и AI",        # openai-inline → 🤖 ChatGPT и AI
     "WatchNet",
     "OpenAI blocks Cloudflare IPs — must not go through WARP"),

    ("notebooklm routing",
     "https://notebooklm.google.com",
     "WARP",                # must go through WARP
     None,
     "Requires Cloudflare IP"),

    ("antigravity routing",
     "https://antigravity.google",
     "WARP",
     None,
     "Requires Cloudflare IP"),

    ("notion routing",
     "https://notion.so",
     "Зарубежные серверы",  # intl-blocked-inline → direct to foreign pool
     "Обход блокировок",    # must NOT hit RU bypass
     "Notion geo-bans RU IPs"),

    ("autodesk routing",
     "https://www.autodesk.com",
     "Зарубежные серверы",  # no-russia-hosts → direct to foreign pool
     "Недоступные из РФ",   # must NOT hit RU bypass (RU exit IP → 403 from Autodesk)
     "Autodesk geo-bans RU IPs — needs foreign server, not RU-bypass"),

    ("adobe routing",
     "https://www.adobe.com",
     "Зарубежные серверы",  # intl-blocked-inline → foreign pool
     "Недоступные из РФ",
     "Adobe geo-bans RU IPs — must NOT hit RU-bypass"),
]

def get_last_mihomo_log_line(domain, container="mihomo-test"):
    """Grab the last mihomo log line mentioning this domain."""
    result = subprocess.run(
        ["docker", "logs", "--tail", "50", container],
        capture_output=True, text=True, timeout=5
    )
    lines = (result.stdout + result.stderr).splitlines()
    # Return last line that mentions the domain
    for line in reversed(lines):
        if domain in line:
            return line
    return ""

def warm_up_providers(timeout=30):
    """Force-update HTTP rule providers so routing rules are loaded before tests."""
    providers = mihomo_api("/providers/rules") or {}
    http_providers = [
        name for name, info in providers.get("providers", {}).items()
        if info.get("vehicleType") == "HTTP"
    ]
    if not http_providers:
        return
    print(f"  Warming up {len(http_providers)} HTTP providers...", end="", flush=True)
    for name in http_providers:
        import urllib.parse
        mihomo_api(f"/providers/rules/{urllib.parse.quote(name)}", "PUT")
    # Wait for downloads to complete
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        data = mihomo_api("/providers/rules") or {}
        loaded = sum(
            1 for n, info in data.get("providers", {}).items()
            if info.get("vehicleType") == "HTTP" and len(info.get("rules", [])) > 0
        )
        print(f" {loaded}/{len(http_providers)}", end="", flush=True)
        if loaded >= len(http_providers) * 0.5:  # at least half loaded
            break
    print(" done")

def run_routing_tests(proxy_port):
    section("ROUTING TESTS (verify correct proxy group via logs)")
    print("  These tests check WHICH group handles traffic, not the HTTP result.")
    print("  Catches regressions even when server IPs are blocked by the provider.\n")

    # Need info-level logging
    mihomo_api("/configs", "PATCH", {"log-level": "info"})

    # Ensure HTTP rule providers are loaded before routing tests
    warm_up_providers(timeout=40)

    for label, url, must_contain, must_not, note in ROUTING_TESTS:
        domain = url.split("//")[1].split("/")[0]

        # Clear slate: make the request
        code, _ = http_get(url, proxy=proxy_port)
        log_line = get_last_mihomo_log_line(domain)

        note_str = f"\n         [{note}]" if note else ""
        if not log_line:
            warn(f"{label}: no log line found for {domain} — try running with mihomo in info mode{note_str}")
            continue

        passed = True
        if must_contain and must_contain not in log_line:
            fail(f"{label}: expected '{must_contain}' in routing log{note_str}\n         Log: {log_line.strip()}")
            passed = False
        if must_not and must_not in log_line:
            fail(f"{label}: found forbidden '{must_not}' in routing log — REGRESSION!{note_str}\n         Log: {log_line.strip()}")
            passed = False
        if passed:
            ok(f"{label}: HTTP {code}  ← {log_line.strip().split('match')[-1].strip()}")

    # Restore warning level
    mihomo_api("/configs", "PATCH", {"log-level": "warning"})

def run_live_tests(proxy_port):
    section("LIVE HTTP TESTS")
    print(f"  Proxy: http://127.0.0.1:{proxy_port}")
    print("  Note: HTTP 403 from some services = server-side IP block, not config bug.\n")

    # Check if mihomo API is up
    ver = mihomo_api("/version")
    if not ver:
        fail(f"Mihomo API not responding on port {API_PORT}")
        return
    ok(f"Mihomo API up: {ver.get('version','?')}")

    for label, url, http_min, http_max, pg, pname, note in LIVE_TESTS:
        if pg and pname:
            r = set_proxy_group(pg, pname)
            if r is None:
                skip(f"{label}: could not set {pg}→{pname}, skipping")
                continue

        code, err = http_get(url, proxy=proxy_port if "direct" not in label else None)

        note_str = f"  [{note}]" if note else ""
        if err:
            fail(f"{label}: connection error — {err}{note_str}")
        elif http_min <= code <= http_max:
            ok(f"{label}: HTTP {code}{note_str}")
        else:
            fail(f"{label}: HTTP {code} (expected {http_min}–{http_max}){note_str}")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Mihomo config anti-regression tests")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config YAML")
    parser.add_argument("--live",   action="store_true",         help="Run live HTTP tests")
    parser.add_argument("--proxy",  type=int, default=PROXY_PORT,help=f"Proxy port (default {PROXY_PORT})")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}")
        sys.exit(1)

    print(f"\n{'═'*55}")
    print(f"  Mihomo Config Anti-Regression Tests")
    print(f"  Config: {cfg_path.name}")
    print(f"{'═'*55}")

    cfg = load_config(cfg_path)

    # Static tests
    test_yaml_valid(cfg)
    test_version_comment(cfg_path)
    test_mode_is_rule(cfg)
    test_dns_mode(cfg)
    test_warp_excluded_from_load_balance(cfg)
    test_ru_nodes_excluded_from_load_balance(cfg)
    test_warp_group_filter(cfg)
    test_claude_not_in_warp_domains(cfg)
    test_rule_order_warp_before_ru_inside(cfg)
    test_rule_order_intl_blocked_before_ru_inside(cfg)
    test_rule_order_adult_before_ru_inside(cfg)
    test_rule_order_openai_before_ru_inside(cfg)
    test_notion_in_intl_blocked(cfg)
    test_no_russia_hosts_provider(cfg)
    test_onlyfans_in_adult_extra(cfg)
    test_google_warp_domains(cfg)
    # v2.2 regression
    test_perplexity_in_openai_inline(cfg)
    test_warp_excluded_from_ai_group(cfg)
    test_node_picker_enabled(cfg)
    test_youtube_aliases(cfg)
    test_hidden_internals(cfg)
    test_nudevista_routed_via_warp(cfg)
    test_de_matrix_udp_bypass(cfg)
    test_group_order_global_first(cfg)

    # Live tests
    if args.live:
        run_routing_tests(args.proxy)
        run_live_tests(args.proxy)

    # Summary
    print(f"\n{'═'*55}")
    if errors:
        print(f"  FAILED: {len(errors)} error(s)")
        for e in errors:
            print(f"    • {e}")
        if warnings:
            print(f"  WARNINGS: {len(warnings)}")
            for w in warnings:
                print(f"    • {w}")
        print(f"{'═'*55}\n")
        sys.exit(1)
    else:
        print(f"  ALL PASSED ({23 + (len(LIVE_TESTS) if args.live else 0)} checks)")
        if warnings:
            print(f"  WARNINGS: {len(warnings)}")
            for w in warnings:
                print(f"    • {w}")
        print(f"{'═'*55}\n")

if __name__ == "__main__":
    main()
