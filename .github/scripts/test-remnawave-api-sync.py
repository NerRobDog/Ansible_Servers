#!/usr/bin/env python3
"""Contract tests for remnawave-api-sync.py.

Covers two guarantees:

1. Template rendering per `remnawave.warp_mode` reproduces the exact panel
   config shapes that exist today (fixtures in testdata/remnawave-profiles are
   sanitized copies of the live profiles - secrets replaced by placeholders).
2. The fail-closed guard refuses any profile write that would REMOVE an
   outbound, an inbound or a routing rule from the live panel config.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
SYNC_SCRIPT = SCRIPT_DIR / "remnawave-api-sync.py"
FIXTURES = SCRIPT_DIR / "testdata" / "remnawave-profiles"
DEFAULT_TEMPLATE = "remnawave/profiles/rw_vless_reality.json"

TEST_INBOUND_TAG = "VLESS_EXAMPLE_NODE"
TEST_SHORT_ID = "00000000000000ff"
TEST_PRIVATE_KEY = "TEST_ONLY_NOT_A_REAL_PRIVATE_KEY_0000000000"
TEST_SERVER_NAME = "node1.example.com"
TEST_REALITY_TARGET = "127.0.0.1:8443"


def load_sync_module() -> Any:
    spec = importlib.util.spec_from_file_location("remnawave_api_sync", SYNC_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RW = load_sync_module()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def host_cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "alias": "example_node",
        "ansible_host": "203.0.113.10",
        "node_secret_key": "",
        "panel_node_uuid": "",
        "target_profile_name": "example_node",
        "target_inbound_tags": [],
        "profile_template": "",
        "warp_mode": "none",
        "warp_inbound_port": RW.DEFAULT_WARP_INBOUND_PORT,
        "allow_destructive_profile_sync": False,
        "inbound_tag": TEST_INBOUND_TAG,
        "reality_target": TEST_REALITY_TARGET,
        "reality_short_id": TEST_SHORT_ID,
        "reality_private_key": TEST_PRIVATE_KEY,
        "reality_server_name": TEST_SERVER_NAME,
        "caddy_domain": TEST_SERVER_NAME,
    }
    cfg.update(overrides)
    return cfg


def render(**overrides: Any) -> dict[str, Any]:
    cfg = host_cfg(**overrides)
    specs = RW.build_profile_specs({cfg["alias"]: cfg}, REPO_ROOT, {}, DEFAULT_TEMPLATE)
    assert_true(len(specs) == 1, "expected exactly one rendered profile spec")
    return specs[0]


def expect_fail(func: Any, needle: str) -> None:
    try:
        func()
    except SystemExit as exc:
        assert_true(exc.code == 1, f"expected exit code 1, got {exc.code}")
        return
    raise AssertionError(f"expected failure containing {needle!r}, but call succeeded")


class FakeClient:
    """Minimal PanelClient stand-in: records every call, answers GETs."""

    def __init__(self, profiles: list[dict[str, Any]], nodes: list[dict[str, Any]] | None = None) -> None:
        self.profiles = profiles
        self.nodes = nodes or []
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        self.calls.append((method.upper(), path, payload))
        if method.upper() == "GET" and path == "config-profiles":
            return {"response": {"configProfiles": copy.deepcopy(self.profiles)}}
        if method.upper() == "GET" and path == "nodes":
            return {"response": copy.deepcopy(self.nodes)}
        return {}

    def writes(self) -> list[tuple[str, str, Any]]:
        return [call for call in self.calls if call[0] in ("POST", "PATCH", "PUT", "DELETE")]


# --------------------------------------------------------------------------
# 1) Template rendering
# --------------------------------------------------------------------------


def test_warp_mode_none_matches_baseline_fixture() -> None:
    spec = render(warp_mode="none")
    assert_true(
        spec["template_rel"] == DEFAULT_TEMPLATE,
        f"warp_mode=none must use the default template, got {spec['template_rel']}",
    )
    assert_true(
        RW.canonical_json(spec["config"]) == RW.canonical_json(load_fixture("baseline")),
        "warp_mode=none render does not match the baseline profile fixture",
    )


def test_warp_mode_all_matches_live_shape() -> None:
    spec = render(warp_mode="all")
    assert_true(
        spec["template_rel"] == RW.WARP_MODE_TEMPLATES["all"],
        f"warp_mode=all selected wrong template: {spec['template_rel']}",
    )
    assert_true(
        RW.canonical_json(spec["config"]) == RW.canonical_json(load_fixture("warp_all")),
        "warp_mode=all render does not match the live dh-germ-1 shape",
    )
    outbound_tags = RW.collect_tags(spec["config"], "outbounds")
    assert_true(
        outbound_tags == ["WARP", "DIRECT", "BLOCK"],
        f"warp_mode=all must put WARP first (default exit), got {outbound_tags}",
    )


def test_warp_mode_inbound_matches_live_shape() -> None:
    spec = render(warp_mode="inbound", warp_inbound_port=2053)
    assert_true(
        spec["template_rel"] == RW.WARP_MODE_TEMPLATES["inbound"],
        f"warp_mode=inbound selected wrong template: {spec['template_rel']}",
    )
    assert_true(
        RW.canonical_json(spec["config"]) == RW.canonical_json(load_fixture("warp_inbound")),
        "warp_mode=inbound render does not match the live tw-germ-1 shape",
    )
    inbound_tags = RW.collect_tags(spec["config"], "inbounds")
    assert_true(
        inbound_tags == [TEST_INBOUND_TAG, f"{TEST_INBOUND_TAG}_WARP"],
        f"warp inbound must keep the _WARP suffix, got {inbound_tags}",
    )
    outbound_tags = RW.collect_tags(spec["config"], "outbounds")
    assert_true(
        outbound_tags == ["DIRECT", "WARP", "BLOCK"],
        f"warp_mode=inbound must keep DIRECT as default exit, got {outbound_tags}",
    )


def test_warp_inbound_port_is_configurable() -> None:
    spec = render(warp_mode="inbound", warp_inbound_port=8443)
    ports = [inbound["port"] for inbound in spec["config"]["inbounds"]]
    assert_true(ports == [443, 8443], f"warp_inbound_port not applied, got ports {ports}")


def test_explicit_profile_template_wins_over_warp_mode() -> None:
    spec = render(warp_mode="all", profile_template=DEFAULT_TEMPLATE)
    assert_true(
        spec["template_rel"] == DEFAULT_TEMPLATE,
        "explicit profile_template must win over warp_mode auto-selection",
    )


def test_invalid_warp_mode_rejected() -> None:
    expect_fail(lambda: render(warp_mode="warp"), "warp_mode")


def test_invalid_warp_inbound_port_rejected() -> None:
    expect_fail(lambda: render(warp_mode="inbound", warp_inbound_port=70000), "warp_inbound_port")


def test_fleet_defaults_both_inbound_tags_for_warp_inbound() -> None:
    fleet = {
        "hosts": {
            "example_node": {
                "ansible_host": "203.0.113.10",
                "remnawave": {
                    "caddy_domain": TEST_SERVER_NAME,
                    "warp_mode": "inbound",
                    "warp_inbound_port": 2053,
                },
            }
        }
    }
    hosts = RW.normalize_fleet_hosts(fleet)
    tags = hosts["example_node"]["target_inbound_tags"]
    assert_true(
        tags == ["VLESS_EXAMPLE_NODE", "VLESS_EXAMPLE_NODE_WARP"],
        f"warp_mode=inbound must activate both inbounds by default, got {tags}",
    )
    assert_true(hosts["example_node"]["warp_mode"] == "inbound", "warp_mode not carried into host cfg")
    assert_true(hosts["example_node"]["warp_inbound_port"] == 2053, "warp_inbound_port not carried into host cfg")


def test_fleet_defaults_stay_unchanged_without_warp() -> None:
    fleet = {
        "hosts": {
            "example_node": {
                "ansible_host": "203.0.113.10",
                "remnawave": {"caddy_domain": TEST_SERVER_NAME},
            }
        }
    }
    hosts = RW.normalize_fleet_hosts(fleet)
    host = hosts["example_node"]
    assert_true(host["warp_mode"] == "none", "warp_mode must default to none")
    assert_true(host["target_inbound_tags"] == ["VLESS_EXAMPLE_NODE"], "default inbound tags changed")
    assert_true(host["profile_template"] == "", "profile_template must stay empty unless set explicitly")
    assert_true(
        RW.resolve_profile_template(host, DEFAULT_TEMPLATE) == DEFAULT_TEMPLATE,
        "warp_mode=none must resolve to the default template",
    )


# --------------------------------------------------------------------------
# 2) Fail-closed guard
# --------------------------------------------------------------------------


def test_guard_detects_warp_outbound_removal() -> None:
    removals = RW.diff_profile_removals(load_fixture("warp_all"), load_fixture("baseline"))
    assert_true(RW.has_removals(removals), "removing the WARP outbound must be detected")
    assert_true(removals["outbounds"] == ["WARP"], f"unexpected outbound removals: {removals['outbounds']}")
    assert_true(
        removals["routing_domain_strategy"] == "IPIfNonMatch",
        "dropping routing.domainStrategy must be detected",
    )
    summary = RW.format_removals(removals)
    assert_true("outbounds=[WARP]" in summary, f"summary missing outbound list: {summary}")
    assert_true("routing.domainStrategy=IPIfNonMatch" in summary, f"summary missing domainStrategy: {summary}")


def test_guard_detects_warp_inbound_and_rule_removal() -> None:
    removals = RW.diff_profile_removals(load_fixture("warp_inbound"), load_fixture("baseline"))
    assert_true(removals["outbounds"] == ["WARP"], f"unexpected outbound removals: {removals['outbounds']}")
    assert_true(
        removals["inbounds"] == [f"{TEST_INBOUND_TAG}_WARP"],
        f"unexpected inbound removals: {removals['inbounds']}",
    )
    assert_true(len(removals["rules"]) == 1, f"expected exactly one removed routing rule: {removals['rules']}")
    assert_true(
        '"outboundTag":"WARP"' in removals["rules"][0],
        f"removed rule is not the WARP rule: {removals['rules'][0]}",
    )


def test_guard_ignores_additive_changes() -> None:
    current = load_fixture("baseline")
    rendered = load_fixture("warp_all")
    removals = RW.diff_profile_removals(current, rendered)
    assert_true(not RW.has_removals(removals), f"additive change must not be blocked: {removals}")


def test_guard_ignores_in_place_edits() -> None:
    current = load_fixture("baseline")
    rendered = copy.deepcopy(current)
    rendered["inbounds"][0]["streamSettings"]["realitySettings"]["target"] = "127.0.0.1:9443"
    removals = RW.diff_profile_removals(current, rendered)
    assert_true(not RW.has_removals(removals), f"in-place edit must not be blocked: {removals}")


def test_upsert_refuses_destructive_write() -> None:
    live = load_fixture("warp_all")
    client = FakeClient([{"uuid": "profile-uuid-1", "name": "example_node", "config": live}])
    spec = {
        "name": "example_node",
        "template_rel": DEFAULT_TEMPLATE,
        "config": load_fixture("baseline"),
        "warp_mode": "none",
        "allow_destructive": False,
    }
    created, updated, drift, blocked = RW.upsert_profiles(client, [spec], write_mode=True)
    assert_true(blocked == ["example_node"], f"profile must be blocked, got {blocked}")
    assert_true(updated == 0 and created == 0, "blocked profile must not count as written")
    assert_true(client.writes() == [], f"blocked profile must not be written, got {client.writes()}")


def test_upsert_allows_destructive_write_with_override() -> None:
    live = load_fixture("warp_all")
    client = FakeClient([{"uuid": "profile-uuid-1", "name": "example_node", "config": live}])
    spec = {
        "name": "example_node",
        "template_rel": DEFAULT_TEMPLATE,
        "config": load_fixture("baseline"),
        "warp_mode": "none",
        "allow_destructive": True,
    }
    created, updated, drift, blocked = RW.upsert_profiles(client, [spec], write_mode=True)
    assert_true(blocked == [], f"override must unblock the write, got {blocked}")
    assert_true(updated == 1, f"expected one profile update, got {updated}")
    writes = client.writes()
    assert_true(len(writes) == 1 and writes[0][0] == "PATCH", f"expected a single PATCH, got {writes}")


def test_upsert_reports_would_remove_in_read_only_mode() -> None:
    live = load_fixture("warp_inbound")
    client = FakeClient([{"uuid": "profile-uuid-1", "name": "example_node", "config": live}])
    spec = {
        "name": "example_node",
        "template_rel": DEFAULT_TEMPLATE,
        "config": load_fixture("baseline"),
        "warp_mode": "none",
        "allow_destructive": False,
    }
    created, updated, drift, blocked = RW.upsert_profiles(client, [spec], write_mode=False)
    assert_true(blocked == ["example_node"], f"read-only plan must still block, got {blocked}")
    assert_true(client.writes() == [], "read-only mode must never write")


def test_upsert_writes_matching_warp_profile_without_blocking() -> None:
    """Once the fleet sets warp_mode, the render matches live and nothing is blocked."""
    live = load_fixture("warp_inbound")
    client = FakeClient([{"uuid": "profile-uuid-1", "name": "example_node", "config": live}])
    spec = render(warp_mode="inbound", warp_inbound_port=2053)
    spec["name"] = "example_node"
    spec["allow_destructive"] = False
    created, updated, drift, blocked = RW.upsert_profiles(client, [spec], write_mode=True)
    assert_true(blocked == [], f"matching render must not be blocked, got {blocked}")
    assert_true(updated == 0, "matching render must not trigger a PATCH")
    assert_true(client.writes() == [], f"matching render must not write, got {client.writes()}")


def test_blocked_profile_skips_node_assignment() -> None:
    nodes = [
        {
            "uuid": "node-uuid-1",
            "name": "example_node",
            "address": "203.0.113.10",
            "configProfile": {
                "activeConfigProfileUuid": "profile-uuid-1",
                "activeInbounds": [{"uuid": "inbound-uuid-1"}, {"uuid": "inbound-uuid-2"}],
            },
        }
    ]
    profiles_by_name = {
        "example_node": {
            "uuid": "profile-uuid-1",
            "name": "example_node",
            "inbounds": [{"tag": TEST_INBOUND_TAG, "uuid": "inbound-uuid-1"}],
        }
    }
    client = FakeClient([], nodes)
    assignments = {
        "example_node": {
            "ansible_host": "203.0.113.10",
            "target_profile_name": "example_node",
            "target_inbound_tags": [TEST_INBOUND_TAG],
            "panel_node_uuid": "node-uuid-1",
        }
    }
    updated, drift = RW.assign_profiles_to_nodes(
        client, assignments, profiles_by_name, write_mode=True, blocked_profiles={"example_node"}
    )
    assert_true(updated == 0 and drift == 0, "blocked profile must not produce node updates")
    assert_true(client.writes() == [], f"blocked profile must not touch node assignment, got {client.writes()}")


def main() -> int:
    tests = [
        test_warp_mode_none_matches_baseline_fixture,
        test_warp_mode_all_matches_live_shape,
        test_warp_mode_inbound_matches_live_shape,
        test_warp_inbound_port_is_configurable,
        test_explicit_profile_template_wins_over_warp_mode,
        test_invalid_warp_mode_rejected,
        test_invalid_warp_inbound_port_rejected,
        test_fleet_defaults_both_inbound_tags_for_warp_inbound,
        test_fleet_defaults_stay_unchanged_without_warp,
        test_guard_detects_warp_outbound_removal,
        test_guard_detects_warp_inbound_and_rule_removal,
        test_guard_ignores_additive_changes,
        test_guard_ignores_in_place_edits,
        test_upsert_refuses_destructive_write,
        test_upsert_allows_destructive_write_with_override,
        test_upsert_reports_would_remove_in_read_only_mode,
        test_upsert_writes_matching_warp_profile_without_blocking,
        test_blocked_profile_skips_node_assignment,
    ]

    for test in tests:
        test()
        print(f"PASS: {test.__name__}")

    print("All remnawave-api-sync contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
