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
import subprocess
import urllib.request
from typing import Any

import yaml

MIHOMO_DEFAULT_UUID = "1ef0346d-7989-4e50-88e6-bbe2b99672fc"
CLASH_USER_AGENT = "clash-meta/1.19.0"


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


def extract_proxies(subscription_yaml: str) -> list[dict[str, Any]]:
    """Pull the proxy list out of a rendered subscription document."""
    # PyYAML quotes a snippet of the offending document in its error message,
    # and this body carries live proxy credentials while the gate's Actions log
    # is public. `from None` is load-bearing: without it the original exception
    # rides along as __context__ and Python prints the snippet anyway.
    try:
        rendered = yaml.safe_load(subscription_yaml)
    except yaml.YAMLError as exc:
        raise RuntimeError(
            f"rendered subscription is not valid YAML ({type(exc).__name__}); "
            "body withheld - it carries live proxy credentials"
        ) from None
    # A plain-text panel error ("Not Found", an interstitial) is valid YAML and
    # parses to a str, so .get() would raise a bare AttributeError. Name the
    # type only - never the body.
    if rendered is not None and not isinstance(rendered, dict):
        raise RuntimeError(
            f"rendered subscription is not a YAML mapping (got {type(rendered).__name__}); "
            "body withheld - it carries live proxy credentials"
        )
    proxies = (rendered or {}).get("proxies") or []
    if not proxies:
        raise RuntimeError("rendered subscription contained no proxies")
    return proxies


def fetch_rendered_proxies(subscription_url: str) -> list[dict[str, Any]]:
    """Read the real proxy list a router would receive.

    Uses curl rather than urllib deliberately: the Python.framework
    interpreters on macOS ship without a CA bundle unless
    "Install Certificates.command" has been run, so urllib raises
    CERTIFICATE_VERIFY_FAILED against the panel host while curl, using the
    system trust store, succeeds. CI is unaffected but local runs must work.

    The response carries live proxy credentials - never log it, never write it
    to an artifact, and never include curl's stderr in an error message because
    the URL itself is a bearer credential.
    """
    # The URL goes in on stdin via `curl --config -` rather than as an argv
    # element: argv is readable through `ps` by any local user for the life of
    # the request, and this URL is itself a bearer credential.
    escaped_url = subscription_url.replace("\\", "\\\\").replace('"', '\\"')
    curl_config = f'url = "{escaped_url}"\nuser-agent = "{CLASH_USER_AGENT}"\n'
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--fail", "--max-time", "30", "--config", "-"],
        input=curl_config,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"subscription fetch failed (curl exit {result.returncode})")
    return extract_proxies(result.stdout)
