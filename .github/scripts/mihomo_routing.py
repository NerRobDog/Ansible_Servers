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
