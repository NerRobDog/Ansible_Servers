#!/usr/bin/env python3
"""Byte-for-byte contract test for roles/warp_exit/templates/warp.conf.j2.

The expected text reproduces /etc/wireguard/warp.conf on tw-germ-1 (2026-09-16)
with its keys replaced. Any drift here means the first role run on tw-germ-1
reports a change and restarts its live WARP tunnel.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import jinja2

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "roles" / "warp_exit" / "templates" / "warp.conf.j2"
PLUGIN = REPO_ROOT / "roles" / "warp_exit" / "filter_plugins" / "wgcf_profile.py"

PROFILE = """[Interface]
PrivateKey = FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
Address = 172.16.0.2/32, 2606:4700:110:882c:3048:50b0:dac:cdfd/128
DNS = 1.1.1.1, 1.0.0.1, 2606:4700:4700::1111, 2606:4700:4700::1001
MTU = 1280
[Peer]
PublicKey = FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = engage.cloudflareclient.com:2408
"""

EXPECTED = (
    "[Interface]\n"
    "PrivateKey = FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=\n"
    "Address = 172.16.0.2/32\n"
    "MTU = 1280\n"
    "Table = off\n"
    "\n"
    "[Peer]\n"
    "PublicKey = FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=\n"
    "AllowedIPs = 0.0.0.0/0, ::/0\n"
    "Endpoint = engage.cloudflareclient.com:2408\n"
    "PersistentKeepalive = 25\n"
)


def load_filter():
    spec = importlib.util.spec_from_file_location("wgcf_profile", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_wgcf_profile


def render(**variables) -> str:
    # Ansible's template module renders with trim_blocks=True and keeps the trailing newline.
    env = jinja2.Environment(trim_blocks=True, keep_trailing_newline=True, undefined=jinja2.StrictUndefined)
    return env.from_string(TEMPLATE.read_text(encoding="utf-8")).render(**variables)


def main() -> int:
    rendered = render(warp_exit_profile=load_filter()(PROFILE), warp_exit_mtu=1280, warp_exit_keepalive=25)
    if rendered != EXPECTED:
        print("FAIL: rendered warp.conf differs from the tw-germ-1 shape", file=sys.stderr)
        print("--- expected", file=sys.stderr)
        print(repr(EXPECTED), file=sys.stderr)
        print("--- rendered", file=sys.stderr)
        print(repr(rendered), file=sys.stderr)
        return 1
    print("PASS: warp.conf.j2 matches the tw-germ-1 shape byte for byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
