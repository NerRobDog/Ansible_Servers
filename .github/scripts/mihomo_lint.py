#!/usr/bin/env python3
"""Referential-integrity checks for a mihomo config.

`mihomo -t` accepts a config whose rules point at groups that do not exist, so
this covers the class it misses: dangling references. Written against the real
config, which contains three shapes that break naive parsers - logic rules with
nested parentheses, `no-resolve` trailing modifiers, and rule targets that are
proxies (`DNS-OUT`) rather than groups.
"""

from __future__ import annotations

import re
from typing import Any

# Split on commas that are NOT inside parentheses, so a logic rule such as
# AND,((RULE-SET,x),(NETWORK,udp)),GROUP keeps its middle field intact.
_TOP_LEVEL_COMMA = re.compile(r",(?![^(]*\))")

# Matches RULE-SET references anywhere, including nested inside logic rules.
_RULESET_REFERENCE = re.compile(r"RULE-SET,([^,()]+)")

# Trailing flags that follow the target rather than being one.
_TARGET_MODIFIERS = {"no-resolve", "src", "dst"}

_BUILTIN_TARGETS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}

# Remnawave surfaces a group named PROXY that no rule targets. Not a defect.
_ALLOWED_ORPHAN_GROUPS = {"PROXY"}

_REGEX_FIELDS = ("filter", "exclude-filter", "exclude-type")


def rule_target(rule: str) -> str:
    """Return the outbound a rule dispatches to."""
    parts = [part.strip() for part in _TOP_LEVEL_COMMA.split(rule)]
    target = parts[-1]
    if target in _TARGET_MODIFIERS and len(parts) >= 2:
        target = parts[-2]
    return target


def lint_config(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a parsed mihomo config."""
    errors: list[str] = []
    warnings: list[str] = []

    groups = {group["name"] for group in config.get("proxy-groups") or []}
    proxies = {proxy["name"] for proxy in config.get("proxies") or []}
    providers = set(config.get("rule-providers") or {})
    rules = list(config.get("rules") or [])

    valid_targets = groups | proxies | _BUILTIN_TARGETS

    referenced_providers: set[str] = set()
    targets: set[str] = set()
    for rule in rules:
        referenced_providers.update(_RULESET_REFERENCE.findall(rule))
        target = rule_target(rule)
        targets.add(target)
        if target not in valid_targets:
            errors.append(f"rule targets unknown outbound {target!r}: {rule}")

    for missing in sorted(referenced_providers - providers):
        errors.append(f"rule references undeclared rule-provider {missing!r}")

    for unused in sorted(providers - referenced_providers):
        warnings.append(f"rule-provider {unused!r} is declared but never referenced")

    members: set[str] = set()
    for group in config.get("proxy-groups") or []:
        for member in group.get("proxies") or []:
            members.add(member)
            if member not in valid_targets:
                errors.append(f"group {group['name']!r} lists unknown member {member!r}")
        for field in _REGEX_FIELDS:
            pattern = group.get(field)
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"group {group['name']!r} has an invalid {field}: {exc}")

    for orphan in sorted(groups - targets - members - _ALLOWED_ORPHAN_GROUPS):
        warnings.append(f"group {orphan!r} is neither a rule target nor a member of any group")

    return errors, warnings
