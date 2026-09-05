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

Две идемпотентные правки за один PATCH:
  1) select-группа "📺 YouTube": алиас-заграница "📺 YT фон / PiP" дефолтом
     (index 0), уводит YouTube с задушенного RKN RU-выхода. RU-премиум-алиас
     "📺 YT без рекламы" остаётся ручным выбором.
  2) NTP durable-фикс: NTP/time-домены в dns.fake-ip-filter + 'DST-PORT,123,DIRECT'
     первым правилом. Без этого клиент за роутером резолвит время в fake-ip
     198.18.x и не может синкнуть UDP-123 -> кривые часы -> TLS Google падает ->
     YouTube 'Нет соединения' (диагноз на Яндекс-модуле за wrt-sh 2026-08-23).
  3) Kinopoisk OTT CDN (trex.media, uma.media) в ru-inline + fake-ip-filter ->
     RU-direct. Иначе домены падают в финальный MATCH -> заграничный выход ->
     CDN geo-блочит RU-контент -> плеер Kinopoisk пустой.

Env: RW_PANEL_API_BASE_URL, RW_PANEL_API_TOKEN
"""

import argparse
import base64
import copy
import io
import json
import os
import ssl
import sys
import urllib.request

GROUP = "📺 YouTube"
FOREIGN_ALIAS = "📺 YT фон / PiP"          # -> 🌍 Зарубежные серверы (баланс)
RU_ALIAS = "📺 YT без рекламы"             # -> 🚫 Недоступные из РФ (RU-first)

# NTP durable-фикс: без реального IP времени и прямого UDP-123 клиент за роутером
# не синкает часы -> кривое время -> TLS Google падает -> YouTube 'Нет соединения'
# (диагноз на Яндекс-модуле за wrt-sh, 2026-08-23).
NTP_DOMAINS = [
    "+.pool.ntp.org", "+.ntp.org",
    "time.android.com", "+.time.android.com",
    "time.google.com", "time.windows.com", "time.apple.com",
    "ntp.yandex.net", "+.gpsonextra.net",
]
NTP_RULE = "DST-PORT,123,DIRECT"

# Kinopoisk/Yandex OTT видео-CDN. Резолвится в RU-IP, но домены не входили ни в
# один RU-rule-set -> финальный MATCH -> заграничный выход -> CDN видит не-RU IP
# -> geo-блок RU-контента -> плеер Kinopoisk пустой (диагноз на Яндекс-модуле за
# wrt-sh 2026-08-23, live rule-match подтверждён контейнером mihomo).
# Пиним в ru-inline (-> ⚪🔵🔴 RU сайты, DIRECT) + fake-ip-filter (real RU IP).
RU_INLINE_NAME = "ru-inline"          # inline rule-provider с payload классических правил
RU_CDN_SUFFIXES = ["trex.media", "uma.media"]
RU_CDN_RULES = [f"DOMAIN-SUFFIX,{s}" for s in RU_CDN_SUFFIXES]
RU_CDN_FAKEIP = [f"+.{s}" for s in RU_CDN_SUFFIXES]

# Gemini/NotebookLM/AI Studio SPOF (beads as-2u8). google-warp-inline прибит к
# группе WARP, в которой один прокси — WatchNet (dh-germ-1). Нода легла
# 2026-08-30 (fsfreeze хостера) -> 2026-09-02, Gemini не работал ни через один
# сервер. Фикс: WARP-группа становится fallback — сначала WatchNet-ноды (как
# было, url-test), при их смерти — 🌍 Зарубежные серверы (баланс). Основной
# маршрут не трогаем, только добавляем запасной.
WARP_GROUP = "🏴‍☠️ Cloudflare WARP"
WARP_NODES_GROUP = "🏴‍☠️ WARP-ноды"          # вынесенный url-test по WatchNet
WARP_FALLBACK = "🌍 Зарубежные серверы (баланс)"
WARP_ORDER = [WARP_NODES_GROUP, WARP_FALLBACK]


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


def ntp_status(text):
    """(домены_в_fake-ip-filter, есть_правило_123) — для отчёта inspect."""
    from ruamel.yaml import YAML
    data = YAML().load(text)
    fif = (data.get("dns") or {}).get("fake-ip-filter", []) or []
    have = set(str(x) for x in fif)
    in_fif = [d for d in NTP_DOMAINS if d in have]
    rules = data.get("rules", []) or []
    has_rule = any(str(r).replace(" ", "") == NTP_RULE.replace(" ", "") for r in rules)
    return in_fif, has_rule


def kp_status(text):
    """(правила_в_ru-inline, домены_в_fake-ip-filter) для Kinopoisk CDN."""
    from ruamel.yaml import YAML
    data = YAML().load(text)
    ru = ((data.get("rule-providers") or {}).get(RU_INLINE_NAME) or {}).get("payload", []) or []
    have_r = set(str(x).replace(" ", "") for x in ru)
    in_ru = [r for r in RU_CDN_RULES if r.replace(" ", "") in have_r]
    fif = set(str(x) for x in (data.get("dns") or {}).get("fake-ip-filter", []) or [])
    in_fif = [d for d in RU_CDN_FAKEIP if d in fif]
    return in_ru, in_fif


def warp_status(text):
    """(type WARP-группы, её proxies, есть_ли_группа_WARP-ноды)."""
    from ruamel.yaml import YAML
    groups = YAML().load(text).get("proxy-groups", []) or []
    by = {g.get("name"): g for g in groups}
    g = by.get(WARP_GROUP) or {}
    return g.get("type"), [str(x) for x in (g.get("proxies") or [])], WARP_NODES_GROUP in by


def warp_ok(text):
    t, pl, has_nodes = warp_status(text)
    return t == "fallback" and pl == WARP_ORDER and has_nodes


def transform(text):
    """Идемпотентно: YT-группа заграницей дефолтом + NTP durable-фикс.
    Обе правки за один load/dump. Возвращает (new_text, changes[])."""
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.width = 100000
    data = y.load(text)
    changes = []

    # 1) YT-группа: заграница дефолтом (index 0)
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
                changes.append(f"YT foreign-first ({FOREIGN_ALIAS})")
            break
    else:
        raise SystemExit(f"Группа {GROUP} не найдена.")

    # 2) NTP durable-фикс
    dns = data.get("dns")
    if not dns or "fake-ip-filter" not in dns:
        raise SystemExit(f"dns.fake-ip-filter не найден. dns keys: {list(dns.keys()) if dns else None}")
    fif = dns["fake-ip-filter"]
    have = set(str(x) for x in fif)
    for d in NTP_DOMAINS:
        if d not in have:
            fif.append(d)
            changes.append(f"fake-ip-filter+={d}")
    rules = data.get("rules")
    if rules is None:
        raise SystemExit("rules не найден.")
    if not any(str(r).replace(" ", "") == NTP_RULE.replace(" ", "") for r in rules):
        rules.insert(0, NTP_RULE)
        changes.append(f"rules+={NTP_RULE}")

    # 3) Kinopoisk OTT CDN -> RU (ru-inline payload + fake-ip-filter)
    rp = data.get("rule-providers") or {}
    ru_inline = rp.get(RU_INLINE_NAME)
    if not ru_inline or "payload" not in ru_inline:
        raise SystemExit(f"rule-provider {RU_INLINE_NAME!r} с payload не найден.")
    pl = ru_inline["payload"]
    have_rules = set(str(x).replace(" ", "") for x in pl)
    for r in RU_CDN_RULES:
        if r.replace(" ", "") not in have_rules:
            pl.append(r)
            changes.append(f"{RU_INLINE_NAME}.payload+={r}")
    have_fif = set(str(x) for x in fif)
    for d in RU_CDN_FAKEIP:
        if d not in have_fif:
            fif.append(d)
            changes.append(f"fake-ip-filter+={d}")

    # 4) WARP-группа -> fallback [WARP-ноды (url-test WatchNet), 🌍 баланс]
    groups = data.get("proxy-groups") or []
    names = [g.get("name") for g in groups]
    if WARP_GROUP not in names:
        raise SystemExit(f"Группа {WARP_GROUP!r} не найдена.")
    if WARP_FALLBACK not in names:
        raise SystemExit(f"Группа {WARP_FALLBACK!r} не найдена.")
    idx = names.index(WARP_GROUP)
    warp = groups[idx]
    if warp.get("type") != "fallback":
        if WARP_NODES_GROUP not in names:
            nodes = type(warp)()
            for k, v in warp.items():
                nodes[k] = copy.deepcopy(v)   # не шарить списки (иначе anchor + loop)
            nodes["name"] = WARP_NODES_GROUP
            groups.insert(idx, nodes)
            changes.append(f"proxy-groups+={WARP_NODES_GROUP} (копия url-test WatchNet)")
        for k in ("include-all", "filter", "exclude-filter", "tolerance", "use"):
            if k in warp:
                del warp[k]
        warp["type"] = "fallback"
        warp.setdefault("url", "https://www.gstatic.com/generate_204")
        warp.setdefault("interval", 300)
        pl = warp.get("proxies")
        if pl is None:
            warp["proxies"] = list(WARP_ORDER)
        else:
            del pl[:]
            for it in WARP_ORDER:
                pl.append(it)
        changes.append(f"{WARP_GROUP}: fallback {WARP_ORDER}")

    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue(), changes


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
    ntp_fif, ntp_rule = ntp_status(text)
    kp_ru, kp_fif = kp_status(text)
    print(f"{GROUP}: {pl}")
    print(f"foreign_first={foreign_first}")
    print(f"NTP: домены={len(ntp_fif)}/{len(NTP_DOMAINS)}, DST-PORT,123,DIRECT={ntp_rule}")
    print(f"KP-CDN: ru-inline={len(kp_ru)}/{len(RU_CDN_RULES)}, fake-ip-filter={len(kp_fif)}/{len(RU_CDN_FAKEIP)}")
    print(f"WARP: {warp_status(text)} ok={warp_ok(text)}")

    with open("live-mihomo-template.yaml", "w", encoding="utf-8") as f:
        f.write(text)

    if args.mode == "inspect":
        print("MODE=inspect — PATCH не шлю.")
        return

    new_text, changed = transform(text)
    if not changed:
        print("apply: уже всё на месте (YT foreign-first + NTP + KP-CDN + WARP fallback), PATCH не нужен.")
        return
    print("changes:", changed)
    new_b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
    code, resp = _req("PATCH", f"{base}/api/subscription-templates", token,
                      {"uuid": uuid, "encodedTemplateYaml": new_b64})
    print(f"PATCH -> {code}")
    if not (200 <= code < 300):
        sys.exit(f"PATCH failed: {resp}")

    after_text = get_yaml(base, token, uuid)
    after = yt_proxies(after_text)
    a_fif, a_rule = ntp_status(after_text)
    a_kp_ru, a_kp_fif = kp_status(after_text)
    print(f"VERIFY {GROUP}: {after}")
    print(f"VERIFY NTP: домены={len(a_fif)}/{len(NTP_DOMAINS)}, rule={a_rule}")
    print(f"VERIFY KP-CDN: ru-inline={len(a_kp_ru)}/{len(RU_CDN_RULES)}, fake-ip-filter={len(a_kp_fif)}/{len(RU_CDN_FAKEIP)}")
    if not (after and after[0] == FOREIGN_ALIAS):
        sys.exit("VERIFY FAIL: YT не foreign-first.")
    if len(a_fif) != len(NTP_DOMAINS) or not a_rule:
        sys.exit("VERIFY FAIL: NTP-фикс не полный.")
    if len(a_kp_ru) != len(RU_CDN_RULES) or len(a_kp_fif) != len(RU_CDN_FAKEIP):
        sys.exit("VERIFY FAIL: KP-CDN-пин не полный.")
    print(f"VERIFY WARP: {warp_status(after_text)}")
    if not warp_ok(after_text):
        sys.exit("VERIFY FAIL: WARP-группа не fallback [WARP-ноды, 🌍 баланс].")
    print("✓ Шаблон: YouTube загран + NTP durable + Kinopoisk CDN на RU + WARP fallback.")


if __name__ == "__main__":
    main()
