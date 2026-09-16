"""Parse a wgcf-generated WireGuard profile into the fields warp.conf is built from."""

from __future__ import annotations

import re

try:
    from ansible.errors import AnsibleFilterError
except ImportError:  # the contract test imports this file without Ansible on sys.path
    class AnsibleFilterError(Exception):
        pass


_ASSIGNMENT = re.compile(r"^([A-Za-z]+)\s*=\s*(.+?)$")


def parse_wgcf_profile(text):
    # Error messages name missing fields only: the profile holds the WARP private key,
    # and a filter error ends up in the deploy log and the Telegram alert tail.
    if not isinstance(text, str) or not text.strip():
        raise AnsibleFilterError("wgcf profile is empty")

    section = None
    fields = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        match = _ASSIGNMENT.match(line)
        if match and section is not None:
            fields[(section, match.group(1).lower())] = match.group(2)

    addresses = [item.strip() for item in fields.get(("interface", "address"), "").split(",") if item.strip()]
    ipv4_addresses = [item for item in addresses if ":" not in item]

    result = {
        "private_key": fields.get(("interface", "privatekey"), ""),
        "peer_public_key": fields.get(("peer", "publickey"), ""),
        "address_v4": ipv4_addresses[0] if ipv4_addresses else "",
        "endpoint": fields.get(("peer", "endpoint"), ""),
    }
    labels = {
        "private_key": "Interface.PrivateKey",
        "peer_public_key": "Peer.PublicKey",
        "address_v4": "Interface.Address (IPv4)",
        "endpoint": "Peer.Endpoint",
    }
    missing = [labels[key] for key, value in result.items() if not value]
    if missing:
        raise AnsibleFilterError("wgcf profile is missing: " + ", ".join(missing))
    return result


class FilterModule:
    def filters(self):
        return {"parse_wgcf_profile": parse_wgcf_profile}
