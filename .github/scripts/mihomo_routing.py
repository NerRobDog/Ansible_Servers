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
