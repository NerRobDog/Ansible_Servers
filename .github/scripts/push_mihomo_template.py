#!/usr/bin/env python3
"""
push_mihomo_template.py — читает/правит MIHOMO subscription-template в Remnawave.

Назначение: durable-фикс маршрутизации YouTube в шаблоне, который панель
(ru.watchd0g.dev) отдаёт роутерам OpenClash как профиль `Watchdog` через
домен раздачи no.watchd0g.dev. Правит ЖИВОЙ шаблон (GET → mutate → PATCH),
а не репную копию, чтобы не затереть расхождения prod↔repo (deploy-gap).

Правка: в select-группе "📺 YouTube" ставит "🌍 Зарубежные серверы (баланс)"
первым элементом (в mihomo select первый proxy = дефолт). RU-выход остаётся
доступен ручным переключением. Идемпотентно: если уже так — PATCH не шлём.

Режимы:
  inspect  — только GET, печать текущего шаблона и состояния YouTube-группы.
  apply    — GET → правка → PATCH → повторный GET-verify.

Env (оба обязательны):
  RW_PANEL_API_BASE_URL  — https://ru.watchd0g.dev
  RW_PANEL_API_TOKEN     — Bearer-токен Remnawave
"""

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.request

TEMPLATE_TYPE = "MIHOMO"
YT_GROUP = "📺 YouTube"
FOREIGN = "🌍 Зарубежные серверы (баланс)"


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
            return json.loads(r.read())
    except urllib.request.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"HTTP {e.code} on {method} {url}:\n{body}", file=sys.stderr)
        raise


def get_template(base, token):
    resp = _req("GET", f"{base}/api/subscription-templates/{TEMPLATE_TYPE}", token)
    inner = resp.get("response", resp) if isinstance(resp, dict) else resp
    return inner


def decode_yaml(inner):
    """Достаёт YAML-текст из объекта шаблона независимо от точного имени поля."""
    b64 = inner.get("encodedTemplateYaml")
    if b64:
        return base64.b64decode(b64).decode("utf-8"), "encodedTemplateYaml"
    # запасные варианты на случай иной схемы
    for k in ("templateYaml", "template", "yaml"):
        if inner.get(k):
            return inner[k], k
    raise SystemExit(f"Не нашёл YAML-поле в шаблоне. Ключи: {list(inner.keys())}")


def youtube_state(text):
    """Возвращает (found, foreign_first, proxies_list) для группы YouTube."""
    from ruamel.yaml import YAML
    y = YAML()
    data = y.load(text)
    for g in data.get("proxy-groups", []) or []:
        if g.get("name") == YT_GROUP:
            pl = list(g.get("proxies", []))
            return True, (bool(pl) and pl[0] == FOREIGN), pl
    return False, False, []


def apply_fix(text):
    """Ставит FOREIGN первым в proxies группы YouTube. Возвращает (new_text, changed)."""
    from ruamel.yaml import YAML
    import io
    y = YAML()
    y.preserve_quotes = True
    y.width = 100000  # не переносить длинные строки
    data = y.load(text)
    changed = False
    for g in data.get("proxy-groups", []) or []:
        if g.get("name") == YT_GROUP:
            pl = g.get("proxies")
            if pl is None:
                raise SystemExit("У группы YouTube нет proxies — правка невозможна.")
            plist = list(pl)
            if FOREIGN not in plist:
                raise SystemExit(f"'{FOREIGN}' нет в proxies YouTube: {plist}")
            if plist[0] != FOREIGN:
                plist.remove(FOREIGN)
                plist.insert(0, FOREIGN)
                # переписываем, сохраняя тип узла ruamel
                del pl[:]
                for item in plist:
                    pl.append(item)
                changed = True
            break
    else:
        raise SystemExit(f"Группа '{YT_GROUP}' не найдена в шаблоне.")
    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue(), changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["inspect", "apply"], default="inspect")
    args = ap.parse_args()

    base = os.environ.get("RW_PANEL_API_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("RW_PANEL_API_TOKEN", "").strip()
    if not base or not token:
        print("Нужны RW_PANEL_API_BASE_URL и RW_PANEL_API_TOKEN.", file=sys.stderr)
        sys.exit(2)

    print(f"GET {base}/api/subscription-templates/{TEMPLATE_TYPE}")
    inner = get_template(base, token)
    print(f"Ключи объекта шаблона: {list(inner.keys())}")
    text, field = decode_yaml(inner)
    print(f"YAML-поле: {field}, длина {len(text)} симв., строк {text.count(chr(10)) + 1}")

    found, foreign_first, pl = youtube_state(text)
    print(f"Группа '{YT_GROUP}': found={found}, foreign_first={foreign_first}")
    print(f"  proxies сейчас: {pl}")

    # выгружаем полный живой шаблон в artifact для сверки
    with open("live-mihomo-template.yaml", "w", encoding="utf-8") as f:
        f.write(text)
    print("Живой шаблон сохранён в live-mihomo-template.yaml (artifact).")

    if args.mode == "inspect":
        print("MODE=inspect — PATCH не отправляю.")
        return

    if not found:
        sys.exit(f"apply прерван: группа '{YT_GROUP}' не найдена.")
    if foreign_first:
        print("apply: уже foreign-first, PATCH не нужен (идемпотентно).")
        return

    new_text, changed = apply_fix(text)
    if not changed:
        print("apply: изменений нет.")
        return

    new_b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
    payload = {"templateType": TEMPLATE_TYPE, field: new_b64}
    print(f"PATCH {base}/api/subscription-templates  (field={field})")
    _req("PATCH", f"{base}/api/subscription-templates", token, payload)
    print("PATCH отправлен. Проверяю...")

    inner2 = get_template(base, token)
    text2, _ = decode_yaml(inner2)
    found2, ff2, pl2 = youtube_state(text2)
    print(f"VERIFY: found={found2}, foreign_first={ff2}")
    print(f"  proxies после: {pl2}")
    if not ff2:
        sys.exit("VERIFY FAIL: после PATCH группа YouTube не foreign-first.")
    print("✓ Готово: YouTube в шаблоне теперь по умолчанию через заграницу.")


if __name__ == "__main__":
    main()
