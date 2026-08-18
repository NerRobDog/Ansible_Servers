#!/usr/bin/env python3
"""
push_mihomo_template.py — durable-фикс маршрутизации YouTube в MIHOMO
subscription-template Remnawave (панель ru.watchd0g.dev; роутеры OpenClash
качают его как профиль Watchdog через домен раздачи no.watchd0g.dev).

Контракт панели (проверен на живой инстанции 2026-08-18):
  * шаблоны адресуются по UUID, не по типу;
  * список:   GET  /api/subscription-templates            -> response.templates[]
  * один:     GET  /api/subscription-templates/{uuid}      -> response.encodedTemplateYaml (base64)
  * update:   PATCH /api/subscription-templates  body {uuid, encodedTemplateYaml}
              (имя НЕ слать — 'Default' зарезервировано, вернёт A172)

Правка select-группы "📺 YouTube": ставит алиас-заграницу "📺 YT фон / PiP"
дефолтом (index 0), уводя YouTube с задушенного RKN RU-выхода. RU-премиум-алиас
"📺 YT без рекламы" остаётся ручным выбором. Идемпотентно.

Env: RW_PANEL_API_BASE_URL, RW_PANEL_API_TOKEN
"""

import argparse
import base64
import io
import json
import os
import ssl
import sys
import urllib.request

GROUP = "📺 YouTube"
FOREIGN_ALIAS = "📺 YT фон / PiP"          # -> 🌍 Зарубежные серверы (баланс)
RU_ALIAS = "📺 YT без рекламы"             # -> 🚫 Недоступные из РФ (RU-first)


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _req(method, url, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {
        "User-Agent": "push_mihomo_template/1.0",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
            return r.getcode(), json.loads(r.read())
    except urllib.request.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def find_uuid(base, token, name):
    code, resp = _req("GET", f"{base}/api/subscription-templates", token)
    if code != 200:
        raise SystemExit(f"list GET {code}: {resp}")
    tpls = (resp.get("response") or {}).get("templates", [])
    mihomo = [t for t in tpls if t.get("templateType") == "MIHOMO"]
    for t in mihomo:
        if t.get("name") == name:
            return t["uuid"]
    raise SystemExit(
        f"MIHOMO-шаблон name={name!r} не найден. Есть: "
        + ", ".join(f"{t.get('name')}({t['uuid'][:8]})" for t in mihomo)
    )


def get_yaml(base, token, uuid):
    code, resp = _req("GET", f"{base}/api/subscription-templates/{uuid}", token)
    if code != 200:
        raise SystemExit(f"template GET {code}: {resp}")
    inner = resp.get("response", resp)
    b64 = inner.get("encodedTemplateYaml")
    if not b64:
        raise SystemExit(f"encodedTemplateYaml пуст. Ключи: {list(inner.keys())}")
    return base64.b64decode(b64).decode("utf-8")


def yt_proxies(text):
    from ruamel.yaml import YAML
    for g in YAML().load(text).get("proxy-groups", []) or []:
        if g.get("name") == GROUP:
            return list(g.get("proxies", []))
    return None


def reorder(text):
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.width = 100000
    data = y.load(text)
    changed = False
    for g in data.get("proxy-groups", []) or []:
        if g.get("name") == GROUP:
            pl = g.get("proxies")
            if pl is None:
                raise SystemExit(f"У {GROUP} нет proxies.")
            cur = list(pl)
            if FOREIGN_ALIAS not in cur:
                raise SystemExit(f"{FOREIGN_ALIAS!r} нет в {GROUP}: {cur}")
            if cur[0] != FOREIGN_ALIAS:
                cur.remove(FOREIGN_ALIAS)
                cur.insert(0, FOREIGN_ALIAS)
                del pl[:]
                for it in cur:
                    pl.append(it)
                changed = True
            break
    else:
        raise SystemExit(f"Группа {GROUP} не найдена.")
    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue(), changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["inspect", "apply"], default="inspect")
    ap.add_argument("--name", default="Default", help="имя MIHOMO-шаблона (по умолч. Default = живой)")
    args = ap.parse_args()

    base = os.environ.get("RW_PANEL_API_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("RW_PANEL_API_TOKEN", "").strip()
    if not base or not token:
        print("Нужны RW_PANEL_API_BASE_URL и RW_PANEL_API_TOKEN.", file=sys.stderr)
        sys.exit(2)

    uuid = find_uuid(base, token, args.name)
    print(f"MIHOMO шаблон {args.name!r} -> {uuid}")
    text = get_yaml(base, token, uuid)
    pl = yt_proxies(text)
    foreign_first = bool(pl) and pl[0] == FOREIGN_ALIAS
    print(f"{GROUP}: {pl}")
    print(f"foreign_first={foreign_first}")

    with open("live-mihomo-template.yaml", "w", encoding="utf-8") as f:
        f.write(text)

    if args.mode == "inspect":
        print("MODE=inspect — PATCH не шлю.")
        return

    if foreign_first:
        print("apply: уже foreign-first, PATCH не нужен.")
        return

    new_text, changed = reorder(text)
    if not changed:
        print("apply: изменений нет.")
        return
    new_b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
    code, resp = _req("PATCH", f"{base}/api/subscription-templates", token,
                      {"uuid": uuid, "encodedTemplateYaml": new_b64})
    print(f"PATCH -> {code}")
    if not (200 <= code < 300):
        sys.exit(f"PATCH failed: {resp}")

    after = yt_proxies(get_yaml(base, token, uuid))
    print(f"VERIFY {GROUP}: {after}")
    if not (after and after[0] == FOREIGN_ALIAS):
        sys.exit("VERIFY FAIL.")
    print("✓ YouTube в шаблоне теперь по умолчанию через заграницу.")


if __name__ == "__main__":
    main()
