#!/usr/bin/env python3
"""
RemaWave panel sync:
1) upsert host-driven config profiles from template files
2) assign profile + inbound UUIDs to existing nodes

This script is intended to run in CI before ansible-playbook.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
TAG_SAFE_RE = re.compile(r"[^A-Z0-9_]+")
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_data_file(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        fail(f"File is empty: {path}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if yaml is None:
            fail(f"File '{path}' is not valid JSON and PyYAML is unavailable.")
        try:
            return yaml.safe_load(raw)
        except Exception as exc:  # pragma: no cover
            fail(f"Unable to parse '{path}' as YAML: {exc}")


def load_optional_json_map(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        fail(f"Profile vars file does not exist: {path}")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"Profile vars file must be JSON object: {exc}")
    if not isinstance(data, dict):
        fail("Profile vars payload must be a JSON object.")
    return data


def normalize_api_base_url(base_url: str) -> str:
    candidate = (base_url or "").strip().rstrip("/")
    if not candidate:
        fail("Panel API base URL is empty. Set RW_PANEL_API_BASE_URL.")
    parsed = urllib.parse.urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        fail(f"Invalid panel API base URL: {base_url!r}")
    if parsed.path.endswith("/api"):
        return candidate
    if parsed.path:
        return f"{candidate}/api"
    return f"{candidate}/api"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def ensure_non_empty_str(value: Any, context: str) -> str:
    result = str(value or "").strip()
    if not result:
        fail(f"Missing required value: {context}")
    return result


def resolve_placeholders(value: Any, placeholder_vars: dict[str, Any], context: str) -> Any:
    if isinstance(value, dict):
        return {
            key: resolve_placeholders(sub_value, placeholder_vars, f"{context}.{key}")
            for key, sub_value in value.items()
        }
    if isinstance(value, list):
        return [resolve_placeholders(item, placeholder_vars, f"{context}[{idx}]") for idx, item in enumerate(value)]
    if not isinstance(value, str):
        return value

    matches = PLACEHOLDER_RE.findall(value)
    if not matches:
        return value

    for placeholder in matches:
        if placeholder not in placeholder_vars:
            fail(f"Unresolved placeholder '{placeholder}' in {context}.")

    if len(matches) == 1 and value.strip() == f"${{{matches[0]}}}":
        return copy.deepcopy(placeholder_vars[matches[0]])

    rendered = value
    for placeholder in matches:
        replacement = placeholder_vars[placeholder]
        if isinstance(replacement, (dict, list)):
            fail(f"Placeholder '{placeholder}' in {context} cannot be embedded into a string.")
        rendered = rendered.replace(f"${{{placeholder}}}", "" if replacement is None else str(replacement))
    return rendered


def parse_limit(limit_value: str, known_hosts: set[str]) -> set[str]:
    raw = (limit_value or "").strip()
    if not raw or raw.lower() == "all":
        return set(known_hosts)
    selected = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = sorted(selected - known_hosts)
    if unknown:
        fail(f"--limit contains unknown host aliases: {', '.join(unknown)}")
    return selected


def extract_response_payload(data: Any) -> Any:
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data


def extract_error_message(raw_body: str) -> str:
    text = raw_body.strip()
    if not text:
        return "no error body"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:300]

    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            return "; ".join(str(item) for item in message)
    return text[:300]


def sanitize_tag_token(text: str) -> str:
    token = TAG_SAFE_RE.sub("_", text.strip().upper())
    token = re.sub(r"_+", "_", token).strip("_")
    return token[:48] if token else "HOST"


def build_default_inbound_tag(host_alias: str) -> str:
    return f"VLESS_{sanitize_tag_token(host_alias)}"


def build_default_profile_name(host_alias: str) -> str:
    # Keep profile names human-readable and tied to fleet host alias.
    return host_alias.strip()


def derive_short_id(seed: str) -> str:
    # Reality shortId supports up to 16 hex chars.
    return hashlib.sha256(f"sid:{seed}".encode("utf-8")).hexdigest()[:16]


def derive_x25519_private_key(seed: str) -> str:
    # Deterministic 32-byte key material with X25519 clamping.
    key = bytearray(hashlib.sha256(f"x25519:{seed}".encode("utf-8")).digest())
    key[0] &= 248
    key[31] &= 127
    key[31] |= 64
    return base64.urlsafe_b64encode(bytes(key)).decode("ascii").rstrip("=")


def normalize_short_id(value: str, context: str) -> str:
    result = str(value or "").strip().lower()
    if not result:
        return ""
    if len(result) > 16 or not HEX_RE.fullmatch(result):
        fail(f"{context} must be hex string (max 16 chars).")
    return result


def ensure_inbound_tags(config: dict[str, Any], tag_base: str) -> list[str]:
    inbounds = config.get("inbounds")
    if not isinstance(inbounds, list) or not inbounds:
        fail("Rendered profile config must include non-empty 'inbounds' array.")

    tags: list[str] = []
    for idx, inbound in enumerate(inbounds, start=1):
        if not isinstance(inbound, dict):
            fail("Each inbound item must be an object.")
        tag = tag_base if idx == 1 else f"{tag_base}_{idx}"
        inbound["tag"] = tag
        tags.append(tag)
    return tags


def detect_duplicate_tags(profile_specs: list[dict[str, Any]]) -> None:
    tag_owner: dict[str, str] = {}
    for spec in profile_specs:
        profile_name = spec["name"]
        config = spec["config"]
        inbounds = config.get("inbounds", [])
        if not isinstance(inbounds, list):
            fail(f"Profile '{profile_name}' has invalid inbounds payload.")
        for inbound in inbounds:
            if not isinstance(inbound, dict):
                continue
            tag = str(inbound.get("tag", "")).strip()
            if not tag:
                continue
            existing = tag_owner.get(tag)
            if existing and existing != profile_name:
                fail(f"Inbound tag '{tag}' is duplicated across profiles '{existing}' and '{profile_name}'. Tags must be globally unique.")
            tag_owner[tag] = profile_name


class PanelClient:
    def __init__(self, base_url: str, token: str, timeout: int) -> None:
        self.base_url = normalize_api_base_url(base_url)
        self.token = token.strip()
        self.timeout = timeout
        if not self.token:
            fail("RW_PANEL_API_TOKEN is empty. API sync requires token auth.")

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url=url, method=method.upper(), data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
                content = response.read()
                if not content:
                    return {}
                try:
                    return json.loads(content.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    fail(f"Invalid JSON from {method} {url}: {exc}")
        except urllib.error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            message = extract_error_message(raw_error)
            fail(f"Panel API call failed ({method} {path}): HTTP {exc.code}: {message}")
        except urllib.error.URLError as exc:
            fail(f"Panel API is unreachable ({method} {path}): {exc.reason}")


def render_profile_template(template_path: Path, placeholder_vars: dict[str, Any]) -> dict[str, Any]:
    raw = template_path.read_text(encoding="utf-8")
    try:
        template_obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"Profile template must be valid JSON: {template_path}: {exc}")
    rendered = resolve_placeholders(template_obj, placeholder_vars, str(template_path))
    unresolved = PLACEHOLDER_RE.findall(canonical_json(rendered))
    if unresolved:
        fail(f"Unresolved placeholders remain in {template_path}: {', '.join(sorted(set(unresolved)))}")
    if not isinstance(rendered, dict):
        fail(f"Profile template root must be a JSON object: {template_path}")
    return rendered


def normalize_manifest(manifest: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        fail("Manifest root must be an object.")
    default_template_rel = str(manifest.get("default_profile_template", "") or "").strip()
    if not default_template_rel:
        fail("Manifest must define non-empty 'default_profile_template'.")
    default_template_path = (repo_root / default_template_rel).resolve()
    if not default_template_path.exists():
        fail(f"default_profile_template not found: {default_template_rel}")

    nodes = manifest.get("nodes", [])
    if nodes is None:
        nodes = []
    if not isinstance(nodes, list):
        fail("Manifest field 'nodes' must be a list.")

    return {
        "default_profile_template": default_template_rel,
        "default_profile_template_path": default_template_path,
        "nodes": nodes,
    }


def normalize_fleet_hosts(fleet_data: dict[str, Any], default_template_rel: str) -> dict[str, dict[str, Any]]:
    hosts = fleet_data.get("hosts")
    if not isinstance(hosts, dict) or not hosts:
        fail("Fleet config must contain non-empty object field 'hosts'.")

    normalized: dict[str, dict[str, Any]] = {}
    for alias, host_cfg in hosts.items():
        if not isinstance(host_cfg, dict):
            fail(f"Fleet host '{alias}' must be an object.")
        ansible_host = ensure_non_empty_str(host_cfg.get("ansible_host", ""), f"hosts.{alias}.ansible_host")

        remnawave = host_cfg.get("remnawave", {})
        if remnawave is None:
            remnawave = {}
        if not isinstance(remnawave, dict):
            fail(f"Fleet host '{alias}' remnawave block must be an object.")

        profile_name = str(remnawave.get("target_profile_name", "") or "").strip()
        if not profile_name:
            profile_name = build_default_profile_name(alias)

        inbound_tag = str(remnawave.get("inbound_tag", "") or "").strip()
        if not inbound_tag:
            inbound_tag = build_default_inbound_tag(alias)

        target_inbound_tags = remnawave.get("target_inbound_tags")
        if target_inbound_tags is None:
            target_inbound_tags = [inbound_tag]
        if not isinstance(target_inbound_tags, list):
            fail(f"Fleet host '{alias}' remnawave.target_inbound_tags must be a list.")
        for tag in target_inbound_tags:
            if not isinstance(tag, str) or not tag.strip():
                fail(f"Fleet host '{alias}' has invalid inbound tag: {tag!r}")

        caddy_domain = str(remnawave.get("caddy_domain", "") or "").strip()
        reality_server_name = str(remnawave.get("reality_server_name", "") or "").strip() or caddy_domain
        reality_target = str(remnawave.get("reality_target", "") or "").strip() or "127.0.0.1:8443"

        normalized[alias] = {
            "alias": alias,
            "ansible_host": ansible_host,
            "node_secret_key": str(remnawave.get("node_secret_key", "") or "").strip(),
            "panel_node_uuid": str(remnawave.get("panel_node_uuid", "") or "").strip(),
            "target_profile_name": profile_name,
            "target_inbound_tags": [str(tag).strip() for tag in target_inbound_tags if str(tag).strip()],
            "profile_template": str(remnawave.get("profile_template", "") or default_template_rel).strip(),
            "inbound_tag": inbound_tag,
            "reality_target": reality_target,
            "reality_short_id": str(remnawave.get("reality_short_id", "") or "").strip(),
            "reality_private_key": str(remnawave.get("reality_private_key", "") or "").strip(),
            "reality_server_name": reality_server_name,
            "caddy_domain": caddy_domain,
        }
    return normalized


def normalize_manifest_nodes(nodes_raw: list[Any], known_hosts: set[str]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(nodes_raw):
        if not isinstance(item, dict):
            fail(f"Manifest nodes[{idx}] must be an object.")
        host_alias = str(item.get("host", "") or "").strip()
        if not host_alias:
            fail(f"Manifest nodes[{idx}] has empty 'host'.")
        if host_alias not in known_hosts:
            fail(f"Manifest nodes[{idx}] refers to unknown host alias '{host_alias}'.")

        inbound_tags = item.get("target_inbound_tags")
        if inbound_tags is not None:
            if not isinstance(inbound_tags, list):
                fail(f"Manifest nodes[{idx}].target_inbound_tags must be a list.")
            for tag in inbound_tags:
                if not isinstance(tag, str) or not tag.strip():
                    fail(f"Manifest nodes[{idx}] has invalid inbound tag: {tag!r}")

        node_cfg: dict[str, Any] = {}
        for key in (
            "panel_node_uuid",
            "target_profile_name",
            "profile_template",
            "inbound_tag",
            "reality_target",
            "reality_short_id",
            "reality_private_key",
            "reality_server_name",
        ):
            if key in item:
                node_cfg[key] = str(item.get(key, "") or "").strip()
        if "target_inbound_tags" in item:
            node_cfg["target_inbound_tags"] = [tag.strip() for tag in (inbound_tags or []) if tag.strip()]
        normalized[host_alias] = node_cfg
    return normalized


def merge_node_assignments(
    fleet_hosts: dict[str, dict[str, Any]],
    manifest_nodes: dict[str, dict[str, Any]],
    selected_hosts: set[str],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for alias, host_cfg in fleet_hosts.items():
        if alias not in selected_hosts:
            continue
        merged[alias] = copy.deepcopy(host_cfg)

    for alias, node_cfg in manifest_nodes.items():
        if alias not in selected_hosts:
            continue
        entry = merged.get(alias)
        if entry is None:
            fail(f"Internal error: host alias '{alias}' was not resolved from fleet.")
        entry.update(node_cfg)

    filtered: dict[str, dict[str, Any]] = {}
    for alias, cfg in merged.items():
        has_assignment = bool(cfg.get("target_profile_name"))
        if has_assignment:
            filtered[alias] = cfg
    return filtered


def build_profile_specs(
    assignments: dict[str, dict[str, Any]],
    repo_root: Path,
    global_placeholder_vars: dict[str, Any],
) -> list[dict[str, Any]]:
    profile_specs_by_name: dict[str, dict[str, Any]] = {}

    for alias, cfg in assignments.items():
        template_rel = ensure_non_empty_str(cfg.get("profile_template", ""), f"host '{alias}' profile_template")
        template_path = (repo_root / template_rel).resolve()
        if not template_path.exists():
            fail(f"Host '{alias}' profile template not found: {template_rel}")

        server_name = str(cfg.get("reality_server_name", "") or "").strip()
        if not server_name:
            fail(f"Host '{alias}' must define remnawave.caddy_domain or remnawave.reality_server_name.")

        profile_name = ensure_non_empty_str(cfg.get("target_profile_name", ""), f"host '{alias}' target_profile_name")
        seed_base = str(cfg.get("node_secret_key", "") or "").strip() or f"{alias}|{profile_name}|{server_name}"

        short_id_raw = normalize_short_id(
            str(cfg.get("reality_short_id", "") or ""),
            f"host '{alias}' remnawave.reality_short_id",
        )
        short_id = short_id_raw or derive_short_id(seed_base)
        private_key = str(cfg.get("reality_private_key", "") or "").strip() or derive_x25519_private_key(seed_base)
        reality_target = ensure_non_empty_str(cfg.get("reality_target", ""), f"host '{alias}' remnawave.reality_target")
        inbound_tag = ensure_non_empty_str(cfg.get("inbound_tag", ""), f"host '{alias}' remnawave.inbound_tag")

        host_vars = {
            "RW_REALITY_TARGET": reality_target,
            "RW_REALITY_SHORT_ID": short_id,
            "RW_REALITY_PRIVATE_KEY": private_key,
            "RW_REALITY_SERVER_NAME": server_name,
            "RW_INBOUND_TAG": inbound_tag,
        }
        render_vars = dict(global_placeholder_vars)
        render_vars.update(host_vars)

        rendered_config = render_profile_template(template_path, render_vars)
        generated_tags = ensure_inbound_tags(rendered_config, inbound_tag)

        target_inbound_tags = cfg.get("target_inbound_tags")
        if not isinstance(target_inbound_tags, list) or not target_inbound_tags:
            cfg["target_inbound_tags"] = generated_tags

        existing = profile_specs_by_name.get(profile_name)
        if existing is not None:
            if canonical_json(existing["config"]) != canonical_json(rendered_config):
                fail(f"Profile name '{profile_name}' is generated by multiple hosts with different configs.")
            continue

        profile_specs_by_name[profile_name] = {
            "name": profile_name,
            "template_rel": template_rel,
            "config": rendered_config,
        }

    profile_specs = list(profile_specs_by_name.values())
    detect_duplicate_tags(profile_specs)
    return profile_specs


def normalize_current_inbound_uuids(active_inbounds: Any) -> list[str]:
    if not isinstance(active_inbounds, list):
        return []
    uuids = []
    for item in active_inbounds:
        if isinstance(item, str) and item.strip():
            uuids.append(item.strip())
            continue
        if isinstance(item, dict):
            uuid_value = str(item.get("uuid", "")).strip()
            if uuid_value:
                uuids.append(uuid_value)
    return sorted(set(uuids))


def upsert_profiles(
    client: PanelClient,
    profile_specs: list[dict[str, Any]],
    write_mode: bool,
) -> tuple[int, int, int]:
    payload = extract_response_payload(client.request("GET", "config-profiles"))
    profile_list = payload.get("configProfiles", []) if isinstance(payload, dict) else []
    if not isinstance(profile_list, list):
        fail("Unexpected response from GET /api/config-profiles.")
    existing_by_name = {str(item.get("name", "")).strip(): item for item in profile_list if isinstance(item, dict)}

    created = 0
    updated = 0
    drift = 0
    for spec in profile_specs:
        rendered_config = spec["config"]
        existing = existing_by_name.get(spec["name"])
        if existing is None:
            if write_mode:
                client.request("POST", "config-profiles", {"name": spec["name"], "config": rendered_config})
                print(f"profile:create:{spec['name']}")
                created += 1
            else:
                print(f"profile:drift:missing:{spec['name']}")
                drift += 1
            continue

        current_config = existing.get("config", {})
        if canonical_json(current_config) != canonical_json(rendered_config):
            if write_mode:
                client.request(
                    "PATCH",
                    "config-profiles",
                    {
                        "uuid": existing.get("uuid"),
                        "name": spec["name"],
                        "config": rendered_config,
                    },
                )
                print(f"profile:update:{spec['name']}")
                updated += 1
            else:
                print(f"profile:drift:config-mismatch:{spec['name']}")
                drift += 1
    return created, updated, drift


def fetch_profiles_by_name(client: PanelClient) -> dict[str, dict[str, Any]]:
    payload = extract_response_payload(client.request("GET", "config-profiles"))
    profile_list = payload.get("configProfiles", []) if isinstance(payload, dict) else []
    if not isinstance(profile_list, list):
        fail("Unexpected response from GET /api/config-profiles.")
    result = {}
    for item in profile_list:
        if not isinstance(item, dict):
            continue
        profile_name = str(item.get("name", "")).strip()
        if profile_name:
            result[profile_name] = item
    return result


def fetch_nodes(client: PanelClient) -> list[dict[str, Any]]:
    payload = extract_response_payload(client.request("GET", "nodes"))
    if not isinstance(payload, list):
        fail("Unexpected response from GET /api/nodes.")
    return [item for item in payload if isinstance(item, dict)]


def build_node_lookup(
    nodes: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    by_uuid = {}
    by_address: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        node_uuid = str(node.get("uuid", "")).strip()
        address = str(node.get("address", "")).strip()
        name = str(node.get("name", "")).strip()
        if node_uuid:
            by_uuid[node_uuid] = node
        if address:
            by_address.setdefault(address, []).append(node)
        if name:
            by_name.setdefault(name, []).append(node)
    return by_uuid, by_address, by_name


def assign_profiles_to_nodes(
    client: PanelClient,
    assignments: dict[str, dict[str, Any]],
    profiles_by_name: dict[str, dict[str, Any]],
    write_mode: bool,
) -> tuple[int, int]:
    nodes = fetch_nodes(client)
    nodes_by_uuid, nodes_by_address, nodes_by_name = build_node_lookup(nodes)

    updated = 0
    drift = 0
    for alias, assignment in assignments.items():
        target_profile_name = str(assignment.get("target_profile_name", "")).strip()
        target_inbound_tags = assignment.get("target_inbound_tags", [])
        panel_node_uuid = str(assignment.get("panel_node_uuid", "")).strip()
        ansible_host = assignment.get("ansible_host", "")

        if not target_profile_name:
            fail(f"Host '{alias}' must define remnawave.target_profile_name for API sync.")
        if not isinstance(target_inbound_tags, list) or not target_inbound_tags:
            fail(f"Host '{alias}' must define non-empty remnawave.target_inbound_tags for API sync.")

        profile = profiles_by_name.get(target_profile_name)
        if profile is None:
            fail(f"Profile '{target_profile_name}' not found for host '{alias}'.")

        profile_uuid = str(profile.get("uuid", "")).strip()
        if not profile_uuid:
            fail(f"Profile '{target_profile_name}' has empty uuid in API response.")

        inbounds = profile.get("inbounds", [])
        if not isinstance(inbounds, list):
            fail(f"Profile '{target_profile_name}' has invalid inbounds payload.")
        inbound_uuid_by_tag = {}
        for inbound in inbounds:
            if not isinstance(inbound, dict):
                continue
            tag = str(inbound.get("tag", "")).strip()
            inbound_uuid = str(inbound.get("uuid", "")).strip()
            if tag and inbound_uuid:
                inbound_uuid_by_tag[tag] = inbound_uuid

        missing_tags = [tag for tag in target_inbound_tags if tag not in inbound_uuid_by_tag]
        if missing_tags:
            fail(
                f"Host '{alias}' requested missing inbound tags in profile '{target_profile_name}': "
                f"{', '.join(missing_tags)}"
            )
        desired_inbound_uuids = [inbound_uuid_by_tag[tag] for tag in target_inbound_tags]

        if panel_node_uuid:
            node = nodes_by_uuid.get(panel_node_uuid)
            if node is None:
                fail(f"Host '{alias}' refers to unknown panel_node_uuid '{panel_node_uuid}'.")
        else:
            alias_name_matches = nodes_by_name.get(alias, [])
            if len(alias_name_matches) > 1:
                fail(
                    f"Host '{alias}' matched multiple panel nodes by name '{alias}'. "
                    "Set remnawave.panel_node_uuid explicitly."
                )
            if alias_name_matches:
                node = alias_name_matches[0]
            else:
                matches = nodes_by_address.get(str(ansible_host), [])
                if not matches:
                    fail(
                        f"Host '{alias}' cannot resolve panel node by name '{alias}' or by address '{ansible_host}'. "
                        "Set remnawave.panel_node_uuid explicitly."
                    )
                if len(matches) > 1:
                    fail(
                        f"Host '{alias}' matched multiple panel nodes by address '{ansible_host}'. "
                        "Set remnawave.panel_node_uuid explicitly."
                    )
                node = matches[0]
            panel_node_uuid = str(node.get("uuid", "")).strip()

        config_profile = node.get("configProfile", {})
        current_profile_uuid = str(config_profile.get("activeConfigProfileUuid", "") or "").strip()
        current_inbound_uuids = normalize_current_inbound_uuids(config_profile.get("activeInbounds"))
        desired_inbound_uuids_sorted = sorted(set(desired_inbound_uuids))

        if current_profile_uuid == profile_uuid and current_inbound_uuids == desired_inbound_uuids_sorted:
            print(f"node:ok:{alias}:{panel_node_uuid}")
            continue

        if write_mode:
            client.request(
                "PATCH",
                "nodes",
                {
                    "uuid": panel_node_uuid,
                    "configProfile": {
                        "activeConfigProfileUuid": profile_uuid,
                        "activeInbounds": desired_inbound_uuids,
                    },
                },
            )
            print(f"node:update:{alias}:{panel_node_uuid}")
            updated += 1
        else:
            print(f"node:drift:{alias}:{panel_node_uuid}")
            drift += 1

    return updated, drift


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync RemaWave config profiles and node assignments.")
    parser.add_argument("--fleet-config", required=True, help="Path to decoded fleet config (YAML/JSON).")
    parser.add_argument("--manifest", default="remnawave/profile-sync.yml", help="Path to profile sync manifest.")
    parser.add_argument("--profile-vars", default="", help="Path to JSON map with optional global placeholder values.")
    parser.add_argument("--panel-api-base-url", default="", help="Panel base URL. Example: https://panel.example.com")
    parser.add_argument("--api-token", default="", help="Panel API token. Falls back to RW_PANEL_API_TOKEN env.")
    parser.add_argument("--limit", default="all", help="Host aliases list (comma-separated) or all.")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout in seconds.")
    parser.add_argument("--write", action="store_true", help="Apply write operations. Without this flag script is read-only.")
    args = parser.parse_args()

    repo_root = Path.cwd()
    fleet_config_path = Path(args.fleet_config)
    manifest_path = Path(args.manifest)
    profile_vars_path = Path(args.profile_vars) if args.profile_vars else None

    if not fleet_config_path.exists():
        fail(f"Fleet config does not exist: {fleet_config_path}")
    if not manifest_path.exists():
        fail(f"Manifest does not exist: {manifest_path}")

    fleet_data = load_data_file(fleet_config_path)
    if not isinstance(fleet_data, dict):
        fail("Fleet config root must be an object.")

    manifest_data = load_data_file(manifest_path)
    manifest_cfg = normalize_manifest(manifest_data, repo_root)

    global_placeholder_vars = load_optional_json_map(profile_vars_path)
    fleet_hosts = normalize_fleet_hosts(fleet_data, manifest_cfg["default_profile_template"])
    selected_hosts = parse_limit(args.limit, set(fleet_hosts.keys()))
    manifest_nodes = normalize_manifest_nodes(manifest_cfg["nodes"], set(fleet_hosts.keys()))
    assignments = merge_node_assignments(fleet_hosts, manifest_nodes, selected_hosts)
    profile_specs = build_profile_specs(assignments, repo_root, global_placeholder_vars)

    api_token = args.api_token or os.getenv("RW_PANEL_API_TOKEN", "")
    panel_base = args.panel_api_base_url or os.getenv("RW_PANEL_API_BASE_URL", "")
    client = PanelClient(panel_base, api_token, args.timeout)

    print(f"sync:mode:{'write' if args.write else 'read-only'}")
    print(f"sync:selected_hosts:{len(selected_hosts)}")
    print(f"sync:profiles:{len(profile_specs)}")
    print(f"sync:node_assignments:{len(assignments)}")

    profile_created, profile_updated, profile_drift = upsert_profiles(client, profile_specs, args.write)
    profiles_by_name = fetch_profiles_by_name(client)
    node_updated, node_drift = assign_profiles_to_nodes(client, assignments, profiles_by_name, args.write)

    if not args.write:
        profile_names = {spec["name"] for spec in profile_specs}
        missing_profiles = sorted(name for name in profile_names if name not in profiles_by_name)
        profile_drift += len(missing_profiles)
        for profile_name in missing_profiles:
            print(f"profile:drift:missing-after-read:{profile_name}")
        total_drift = profile_drift + node_drift
        print(f"sync:drift_total:{total_drift}")
        if total_drift > 0:
            fail("Read-only sync detected drift. Re-run with --write to apply changes.")

    print(
        "sync:summary:"
        f"profile_created={profile_created},"
        f"profile_updated={profile_updated},"
        f"node_updated={node_updated},"
        f"node_drift={node_drift}"
    )


if __name__ == "__main__":
    main()
