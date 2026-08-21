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
