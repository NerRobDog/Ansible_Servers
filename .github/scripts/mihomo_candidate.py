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
