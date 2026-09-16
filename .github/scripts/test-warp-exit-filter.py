#!/usr/bin/env python3
"""Contract tests for the warp_exit parse_wgcf_profile filter."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN = REPO_ROOT / "roles" / "warp_exit" / "filter_plugins" / "wgcf_profile.py"

# Same layout wgcf 2.2.x writes on tw-germ-1: no blank line between sections,
# dual-stack Address, DNS line present. Keys here are fake.
PROFILE_TW_GERM_1_SHAPE = """[Interface]
PrivateKey = FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
Address = 172.16.0.2/32, 2606:4700:110:882c:3048:50b0:dac:cdfd/128
DNS = 1.1.1.1, 1.0.0.1, 2606:4700:4700::1111, 2606:4700:4700::1001
MTU = 1280
[Peer]
PublicKey = FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = engage.cloudflareclient.com:2408
"""


def load_plugin():
    spec = importlib.util.spec_from_file_location("wgcf_profile", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_error(func, text: str, must_mention: str) -> None:
    try:
        func(text)
    except Exception as exc:  # AnsibleFilterError or the local fallback class
        message = str(exc)
        assert_true(must_mention in message, f"Error should mention {must_mention!r}, got: {message}")
        assert_true("FAKE" not in message, f"Error must not leak key material, got: {message}")
        return
    raise AssertionError(f"Expected an error mentioning {must_mention!r}")


def test_parses_tw_germ_1_shape() -> None:
    parsed = load_plugin().parse_wgcf_profile(PROFILE_TW_GERM_1_SHAPE)
    assert_true(parsed == {
        "private_key": "FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
        "peer_public_key": "FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=",
        "address_v4": "172.16.0.2/32",
        "endpoint": "engage.cloudflareclient.com:2408",
    }, f"Unexpected parse result: {parsed}")


def test_keys_are_scoped_to_their_section() -> None:
    # PublicKey under [Interface] must not be taken as the peer key.
    text = PROFILE_TW_GERM_1_SHAPE.replace("[Peer]\nPublicKey = FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=\n", "[Peer]\n")
    text = text.replace("MTU = 1280\n", "MTU = 1280\nPublicKey = FAKEwrongSECTION=\n")
    expect_error(load_plugin().parse_wgcf_profile, text, "Peer.PublicKey")


def test_missing_ipv4_address_rejected() -> None:
    text = PROFILE_TW_GERM_1_SHAPE.replace("172.16.0.2/32, ", "")
    expect_error(load_plugin().parse_wgcf_profile, text, "IPv4")


def test_missing_private_key_rejected() -> None:
    text = PROFILE_TW_GERM_1_SHAPE.replace("PrivateKey = FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=\n", "")
    expect_error(load_plugin().parse_wgcf_profile, text, "Interface.PrivateKey")


def test_empty_profile_rejected() -> None:
    expect_error(load_plugin().parse_wgcf_profile, "   \n", "empty")


def test_registered_as_ansible_filter() -> None:
    filters = load_plugin().FilterModule().filters()
    assert_true("parse_wgcf_profile" in filters, f"Filter not registered: {sorted(filters)}")


def main() -> int:
    tests = [
        test_parses_tw_germ_1_shape,
        test_keys_are_scoped_to_their_section,
        test_missing_ipv4_address_rejected,
        test_missing_private_key_rejected,
        test_empty_profile_rejected,
        test_registered_as_ansible_filter,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All warp_exit filter contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
