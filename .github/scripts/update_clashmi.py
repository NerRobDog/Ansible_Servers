#!/usr/bin/env python3
"""
update_clashmi.py — обновляет версии Clash MI в subscription-page-config Remnawave

Использование:
  python3 update_clashmi.py [--dry-run] [--local /path/to/Subscribe.json]

Переменные окружения (обязательны в режиме API):
  RW_PANEL_API_BASE_URL  — например https://ru.watchd0g.dev
  RW_PANEL_API_TOKEN     — Bearer-токен Remnawave

Режим --local: обновляет файл на диске без обращения к API (для тестов).
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

CLASHMI_API = "https://api.github.com/repos/KaringX/clashmi/releases/latest"
VERSION_RE = re.compile(r"clashmi_(\d+\.\d+\.\d+\.\d+)_")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _ssl_ctx():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    return ctx


def http_get(url: str, token = None) :
    headers = {"User-Agent": "update_clashmi/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as r:
            return json.loads(r.read())
    except Exception:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "15", "-H", f"Authorization: Bearer {token or ''}", url],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)


def http_patch(url: str, payload: dict, token: str) :
    data = json.dumps(payload).encode()
    headers = {
        "User-Agent": "update_clashmi/1.0",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            return json.loads(r.read())
    except urllib.request.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        raise


# ── Core logic ────────────────────────────────────────────────────────────────

def get_latest_clashmi_version() :
    data = http_get(CLASHMI_API)
    return data["tag_name"].lstrip("v")


def find_current_version(content: str) :
    m = VERSION_RE.search(content)
    return m.group(1) if m else None


def apply_update(content: str, old_ver: str, new_ver: str) :
    return content.replace(f"clashmi_{old_ver}_", f"clashmi_{new_ver}_")


# ── Modes ─────────────────────────────────────────────────────────────────────

def mode_local(path: Path, dry_run: bool) :
    content = path.read_text(encoding="utf-8")
    current = find_current_version(content)
    if not current:
        print("Версионные ссылки Clash MI не найдены.")
        return False

    print(f"Проверяю актуальную версию на GitHub...")
    latest = get_latest_clashmi_version()
    print(f"  В файле: {current}  |  GitHub: {latest}")

    if current == latest:
        print("  Файл актуален.")
        return False

    new_content = apply_update(content, current, latest)
    changed = content.count(f"clashmi_{current}_")
    print(f"  Обновляю {changed} ссылок: {current} → {latest}")
    if not dry_run:
        path.write_text(new_content, encoding="utf-8")
        print(f"  Сохранено: {path}")
    else:
        print("  [dry-run] Файл не изменён.")
    return True


def mode_api(base_url: str, token: str, dry_run: bool) :
    base_url = base_url.rstrip("/")

    print(f"Получаю список subscription-page-configs из {base_url}...")
    resp = http_get(f"{base_url}/api/subscription-page-configs", token=token)

    # Листинг возвращает config: null — нужно подгружать каждый конфиг по UUID отдельно
    raw_list = resp.get("response", resp) if isinstance(resp, dict) else resp
    if isinstance(raw_list, dict):
        raw_list = raw_list.get("configs", [])
    if not raw_list:
        print("Конфиги не найдены.", file=sys.stderr)
        return False

    print(f"Найдено конфигов: {len(raw_list)}")

    print("Проверяю актуальную версию Clash MI на GitHub...")
    latest = get_latest_clashmi_version()
    print(f"  Последняя версия на GitHub: {latest}")

    updated_any = False
    for stub in raw_list:
        uuid = stub.get("uuid") or stub.get("id")
        name = stub.get("name", uuid)

        # Подгружаем полный конфиг по UUID
        full = http_get(f"{base_url}/api/subscription-page-configs/{uuid}", token=token)
        cfg = full.get("response", full) if isinstance(full, dict) else full
        inner = cfg.get("config")
        if not inner:
            print(f"  [{name}] config пустой — пропускаю.")
            continue

        content_str = json.dumps(inner)
        current = find_current_version(content_str)

        if not current:
            print(f"  [{name}] Ссылок Clash MI нет — пропускаю.")
            continue

        print(f"  [{name}] В конфиге: {current}")

        if current == latest:
            print(f"  [{name}] Актуален.")
            continue

        new_content_str = apply_update(content_str, current, latest)
        changed_count = content_str.count(f"clashmi_{current}_")
        new_inner = json.loads(new_content_str)

        print(f"  [{name}] Обновляю {changed_count} ссылок: {current} → {latest}")

        if dry_run:
            print(f"  [{name}] [dry-run] PATCH не отправлен.")
        else:
            http_patch(
                f"{base_url}/api/subscription-page-configs",
                {"uuid": uuid, "name": name, "config": new_inner},
                token=token,
            )
            print(f"  [{name}] ✓ Обновлено через API.")

        updated_any = True

    return updated_any


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Обновляет версии Clash MI в Remnawave")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--local", metavar="PATH", help="Обновить файл на диске вместо API")
    args = parser.parse_args()

    if args.local:
        changed = mode_local(Path(args.local), dry_run=args.dry_run)
    else:
        base_url = os.environ.get("RW_PANEL_API_BASE_URL", "").strip()
        token = os.environ.get("RW_PANEL_API_TOKEN", "").strip()
        if not base_url or not token:
            print("Нужны RW_PANEL_API_BASE_URL и RW_PANEL_API_TOKEN.", file=sys.stderr)
            sys.exit(1)
        changed = mode_api(base_url, token, dry_run=args.dry_run)

    # Пишем в GITHUB_OUTPUT если запущены в CI
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"updated={'true' if changed else 'false'}\n")

    # exit 0 всегда (ошибки выбрасывают исключения выше)
    sys.exit(0)


if __name__ == "__main__":
    main()
