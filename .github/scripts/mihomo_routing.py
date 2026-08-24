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
import shutil
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


# Pinned deliberately, and bumping it is a deliberate act. The gate's whole
# job is telling a change apart from a regression, so an upstream log-format
# change would turn every domain into "no route observed" and red a PR that
# changed nothing. v1.19.23 is also the core version the fleet's routers run:
# v1.19.30 is known to break REALITY authentication to amalthea.watchd0g.dev,
# so the gate must not silently start testing on it.
MIHOMO_IMAGE = "metacubex/mihomo:v1.19.23"
_CONTAINER_NAME = "mihomo-gate-harness"
_PROXY_PORT = 17890
_READY_TIMEOUT_SECONDS = 60

# Proves the CONNECT+TLS tunnel is serving, which is the path the real probes
# use. A plain-http warmup is useless here: proxying http:// is absolute-URI
# forwarding, ready at ~0.03s, while https CONNECT is not usable until
# ~0.5-0.8s. Measured over three runs on Docker Desktop.
_WARMUP_URL = "https://www.gstatic.com/generate_204"


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)


def _proxy_usable(port: int) -> bool:
    """True once a request actually completes through the mixed port.

    A bare TCP connect is not enough: Docker publishes the host port
    immediately, so it accepts long before mihomo serves it. Measured on Docker
    Desktop, the controller answers at 0.01s while a proxied request still
    fails at 0.05s with curl exit 35 (TLS) and only succeeds from ~0.5s.
    """
    result = _run([
        "curl", "--silent", "--output", "/dev/null", "--max-time", "5",
        "--proxy", f"http://127.0.0.1:{port}", _WARMUP_URL,
    ])
    return result.returncode == 0


def _wait_until_ready(
    secret: str,
    proxy_port: int = _PROXY_PORT,
    timeout: int = _READY_TIMEOUT_SECONDS,
) -> None:
    """Wait until BOTH the Clash API and the proxy port are actually serving."""
    request = urllib.request.Request(
        "http://127.0.0.1:19099/version", headers={"Authorization": f"Bearer {secret}"}
    )
    deadline = time.monotonic() + timeout
    controller_ready = False
    while time.monotonic() < deadline:
        if not controller_ready:
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    json.loads(response.read().decode("utf-8"))
                controller_ready = True
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.5)
                continue
        if _proxy_usable(proxy_port):
            return
        time.sleep(0.5)
    stage = "proxy port" if controller_ready else "control API"
    raise RuntimeError(f"mihomo {stage} did not become ready within {timeout}s")


def _drive(domains: Iterable[str]) -> None:
    """Send one request per domain through the harness proxy."""
    for domain in domains:
        _run([
            "curl", "--silent", "--output", "/dev/null", "--max-time", "20",
            "--proxy", f"http://127.0.0.1:{_PROXY_PORT}", f"https://{domain}",
        ])


def _read_routes() -> dict[str, Route]:
    logs = _run(["docker", "logs", _CONTAINER_NAME])
    return parse_routes(logs.stdout + logs.stderr)


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
            # Loopback-only: published on 0.0.0.0 this is an unauthenticated
            # proxy out through the fleet's exit nodes for anyone on the LAN,
            # plus the control API behind a secret that lives in the source.
            "-p", f"127.0.0.1:{_PROXY_PORT}:7890", "-p", "127.0.0.1:19099:9099",
            "-v", f"{workdir}:/cfg", MIHOMO_IMAGE, "-d", "/cfg",
        ])
        if started.returncode != 0:
            raise RuntimeError(f"failed to start mihomo: {started.stderr.strip()}")

        _wait_until_ready(secret)

        _drive(domains)
        routes = _read_routes()

        # A domain that produced no log line is indistinguishable from a real
        # routing change downstream, and with a dozen third-party endpoints a
        # transient upstream failure is a matter of time. Retry those once.
        missing = [domain for domain in domains if domain not in routes]
        if missing:
            _drive(missing)
            routes = {**routes, **_read_routes()}

        return routes
    finally:
        _run(["docker", "rm", "-f", _CONTAINER_NAME])
        # The candidate on disk is the rendered subscription: real servers,
        # real UUIDs, real passwords. Do not leave copies under /var/folders.
        shutil.rmtree(workdir, ignore_errors=True)


def config_test(candidate: dict[str, Any]) -> tuple[bool, str]:
    """Run `mihomo -t` against the candidate. Returns (ok, output)."""
    workdir = tempfile.mkdtemp(prefix="mihomo-test-")
    try:
        config_path = Path(workdir) / "config.yaml"
        with config_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(candidate, handle, allow_unicode=True, sort_keys=False)
        result = _run([
            "docker", "run", "--rm", "-v", f"{workdir}:/cfg", MIHOMO_IMAGE, "-d", "/cfg", "-t",
        ])
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    finally:
        # Same reason as probe_routes: this file carries live credentials.
        shutil.rmtree(workdir, ignore_errors=True)
