#!/usr/bin/env python3
"""
mihomo2xray.py — конвертер mihomo-конфига подписки в полный Xray JSON.

Зачем: клиенты на xray-ядре (INCY и др.) не понимают наши proxy-groups, но
принимают полный конфиг (inbounds + outbounds + routing). Полный конфиг едет в
xray-core почти без правок и показывается в приложении ОДНОЙ строкой — юзеру
нечего выбирать, только кнопка подключения. Это и есть цель переезда.

Два режима вывода:

  * ``--mode template`` — Xray JSON с Remnawave-директивами ``injectHosts``.
    Панель сама подставит серверы юзера в outbounds по ``remarkRegex`` и
    раздаст пер-юзерный конфиг. Это конечный артефакт для панели.

  * ``--mode static --links links.txt`` — конкретные outbounds, собранные из
    списка vless-ссылок (тело обычной подписки). Для пилота: положить статикой
    и проверить на одном устройстве, не трогая прод.

Соответствие сущностей::

    mihomo                                  xray
    ------------------------------------    -----------------------------------
    proxy-group type: url-test              balancer strategy leastPing
    proxy-group type: load-balance          balancer (см. FOREIGN_STRATEGY)
    proxy-group type: select                разрешается в proxies[0]
    include-all + filter: "(?i)WatchNet"    injectHosts remarkRegex -> tagPrefix
    RULE-SET,<provider>,<группа>            routing.rule + balancerTag/outboundTag
    rule-provider .mrs (бинарный)           geosite:/geoip: категория (PROVIDERS)
    rule-provider .yaml/.txt                инлайн-список (скачивается)
    rule-provider type: inline              инлайн-список из самого конфига

Известные расхождения (проверить на пилоте, см. as-y6d):

  * ``consistent-hashing`` в xray отсутствует — см. FOREIGN_STRATEGY.
  * ``fallback`` (пробовать по порядку, первый живой) отсутствует — группа
    "🚫 Недоступные из РФ" вырождается в balancer над обоими пулами.
  * PROCESS-NAME / PROCESS-NAME-REGEX не поддерживаются на мобильных — правила
    отбрасываются (перечисляются в отчёте).
  * Пер-групповой DNS (nameserver-policy с привязкой резолвера к выходу)
    не воспроизводится: в xray транспорт DNS определяется маршрутом до самого
    DoH-сервера, а у нас во всех политиках один и тот же 1.1.1.1/8.8.8.8.
    Оставлена двухуровневая схема: RU-домены через Яндекс напрямую, остальное
    через Cloudflare DoH.

Геофайлы: категории берутся из runetfreedom/russia-v2ray-rules-dat (обновление
раз в 6 часов, содержит все категории v2fly/domain-list-community плюс
ru-available-only-inside, refilter, ru-blocked). URL для клиента задаётся
профилем маршрутизации, а не этим конфигом.

Использование::

    python3 scripts/mihomo2xray.py mihomo-remnawave-davoyan-rubypass-v2.1.yaml \
        --mode template --out build/xray-template.json

    python3 scripts/mihomo2xray.py mihomo-...-v2.1.yaml \
        --mode static --links my-sub.txt --out build/pilot.json
"""

import argparse
import base64
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request

import yaml

# --- Пулы серверов -----------------------------------------------------------
# Имя mihomo-группы -> (префикс тега outbound-ов, стратегия балансировщика).
# Регексы отбора берутся из самой группы (filter / exclude-filter), поэтому
# правку списка нод в mihomo не нужно дублировать здесь.
POOLS = {
    "🇷🇺 Обход блокировок РФ (авто)": ("ru", "leastPing"),
    "🌍 Зарубежные серверы (баланс)": ("foreign", "leastPing"),
    "🏴‍☠️ Cloudflare WARP": ("warp", "leastPing"),
}

# mihomo балансирует заграницу через consistent-hashing: один и тот же сайт
# всегда уходит через одну и ту же ноду. В xray такого нет. leastPing держит
# один стабильный выход (сессии не рвутся), roundRobin размазывает нагрузку,
# но ломает сервисы, сверяющие постоянство IP.
FOREIGN_STRATEGY = "leastPing"

# Группы-агрегаторы без своего пула: селектор над другими группами.
AGGREGATES = {
    "🚫 Недоступные из РФ": ("blocked", ["ru", "foreign"], "leastPing"),
}

TERMINALS = {
    "DIRECT": ("outboundTag", "direct"),
    "REJECT": ("outboundTag", "block"),
    "🇷🇺 Без VPN": ("outboundTag", "direct"),
    "DNS-OUT": (None, None),  # DNS-хайджек делает сам xray
}

# --- Соответствие rule-provider -> матчер xray -------------------------------
# domain/ip  — готовая гео-категория, ничего качать не надо.
# fetch      — списка нет в geosite.dat, качаем и инлайним.
# protocol   — выражается сниффингом протокола, домены не нужны.
# drop       — сознательно отбрасываем, с причиной в отчёте.
PROVIDERS = {
    "geosite-private": {"domain": ["geosite:private"]},
    "ru-inside": {"domain": ["geosite:ru-available-only-inside"]},
    "refilter-domains": {"domain": ["geosite:refilter"]},
    "youtube": {"domain": ["geosite:youtube"]},
    "telegram-domains": {"domain": ["geosite:telegram"]},
    "telegram-ips": {"ip": ["geoip:telegram"]},
    "discord-domains": {"domain": ["geosite:discord"]},
    "ai": {"domain": ["geosite:category-ai-!cn"]},
    "adult": {"domain": ["geosite:category-porn"]},
    "geosite-ru": {"domain": ["geosite:category-ru"]},
    "torrent-trackers": {"protocol": ["bittorrent"]},
    "discord-voiceips": {"fetch": "mrs"},
    "no-russia-hosts": {"fetch": "hosts"},
    "torrent-clients": {"fetch": "classical"},
    "ru-apps": {"fetch": "classical"},
    "games": {"fetch": "classical"},
}


class Report:
    """Копит всё, что не переехало один в один, чтобы вывести в конце."""

    def __init__(self):
        self.dropped = []
        self.warnings = []
        self.fetched = []

    def drop(self, what, why):
        self.dropped.append(f"{what} — {why}")

    def warn(self, text):
        self.warnings.append(text)

    def render(self):
        out = []
        if self.fetched:
            out.append("Скачано и инлайнено:")
            out += [f"  + {x}" for x in self.fetched]
        if self.dropped:
            out.append("Отброшено:")
            out += [f"  - {x}" for x in self.dropped]
        if self.warnings:
            out.append("Расхождения:")
            out += [f"  ! {x}" for x in self.warnings]
        return "\n".join(out)


def _ctx():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch_lines(url):
    req = urllib.request.Request(url, headers={"User-Agent": "mihomo2xray/1.0"})
    with urllib.request.urlopen(req, timeout=60, context=_ctx()) as resp:
        return resp.read().decode("utf-8", "replace").splitlines()


def parse_classical(entries, report, source):
    """Классические mihomo-правила (DOMAIN-SUFFIX,x / IP-CIDR,x / ...) -> матчер xray."""
    domains, ips = [], []
    for raw in entries:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("- ").strip().strip("'\"")
        parts = [p.strip() for p in line.split(",")]
        kind = parts[0].upper()
        value = parts[1] if len(parts) > 1 else ""
        if not value:
            continue
        if kind == "DOMAIN-SUFFIX":
            domains.append(f"domain:{value}")
        elif kind == "DOMAIN":
            domains.append(f"full:{value}")
        elif kind == "DOMAIN-KEYWORD":
            domains.append(value)
        elif kind == "DOMAIN-REGEX":
            domains.append(f"regexp:{value}")
        elif kind in ("IP-CIDR", "IP-CIDR6"):
            ips.append(value)
        elif kind.startswith("PROCESS"):
            report.drop(f"{source}: {kind},{value}", "xray не матчит процессы на мобильных")
        else:
            report.drop(f"{source}: {kind},{value}", "нет эквивалента в xray")
    matcher = {}
    if domains:
        matcher["domain"] = domains
    if ips:
        matcher["ip"] = ips
    return matcher


def parse_hosts(lines):
    """hosts.txt (`0.0.0.0 example.com`) -> список доменов."""
    domains = []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        host = parts[1] if len(parts) > 1 else parts[0]
        if "." in host and not host.startswith("0.0.0.0"):
            domains.append(f"domain:{host}")
    return {"domain": domains}


def build_provider_matchers(cfg, report, offline):
    """rule-provider -> матчер xray. Инлайны берём из конфига, списки — из сети."""
    matchers = {}
    for name, spec in (cfg.get("rule-providers") or {}).items():
        rule = PROVIDERS.get(name)
        if spec.get("type") == "inline":
            matchers[name] = parse_classical(spec.get("payload") or [], report, name)
            continue
        if rule is None:
            report.drop(f"rule-provider {name}", "нет соответствия в PROVIDERS")
            continue
        if "domain" in rule or "ip" in rule or "protocol" in rule:
            matchers[name] = dict(rule)
            continue
        how = rule.get("fetch")
        if how == "mrs":
            report.drop(f"rule-provider {name}", "бинарный .mrs без гео-категории")
            continue
        if offline:
            report.drop(f"rule-provider {name}", "нужна сеть, запущено с --offline")
            continue
        url = spec.get("url")
        lines = fetch_lines(url)
        if how == "hosts":
            matchers[name] = parse_hosts(lines)
        else:
            payload = yaml.safe_load("\n".join(lines)) or {}
            entries = payload.get("payload", payload) if isinstance(payload, dict) else payload
            matchers[name] = parse_classical(entries, report, name)
        count = len(matchers[name].get("domain", [])) + len(matchers[name].get("ip", []))
        report.fetched.append(f"{name}: {count} записей из {url}")
    return matchers


def resolve_group(name, groups, report, seen=None):
    """Группа -> терминальная цель xray. select/fallback разрешаются в proxies[0]."""
    if name in TERMINALS:
        return TERMINALS[name]
    if name in POOLS:
        return ("balancerTag", POOLS[name][0])
    if name in AGGREGATES:
        return ("balancerTag", AGGREGATES[name][0])
    seen = seen or set()
    if name in seen:
        report.warn(f"цикл в группах на {name}")
        return (None, None)
    seen.add(name)
    group = groups.get(name)
    if not group:
        report.drop(f"группа {name}", "не найдена в proxy-groups")
        return (None, None)
    members = group.get("proxies") or []
    if not members:
        report.drop(f"группа {name}", "пустой список proxies")
        return (None, None)
    if group.get("type") in ("select", "fallback") and len(members) > 1:
        report.warn(
            f"{name}: {group['type']} над {members} -> берётся первый ({members[0]}), "
            "переключение вручную у юзера пропадает"
        )
    return resolve_group(members[0], groups, report, seen)


def build_rules(cfg, matchers, groups, report):
    xray_rules = []
    for raw in cfg.get("rules") or []:
        parts = [p.strip() for p in str(raw).split(",")]
        kind = parts[0].upper()
        if kind == "MATCH":
            key, target = resolve_group(parts[1], groups, report)
            if key:
                xray_rules.append({"type": "field", "network": "tcp,udp", key: target})
            continue
        if kind == "DST-PORT" and parts[2] == "DNS-OUT":
            continue  # DNS-хайджек берёт на себя сам xray
        if kind.startswith("PROCESS"):
            report.drop(f"rule {raw}", "xray не матчит процессы на мобильных")
            continue
        if kind == "AND":
            report.drop(f"rule {raw}", "составное правило, переносить руками")
            continue
        if kind != "RULE-SET":
            report.drop(f"rule {raw}", "тип правила не поддержан конвертером")
            continue
        provider, group_name = parts[1], parts[2]
        matcher = matchers.get(provider)
        if not matcher:
            report.drop(f"rule {raw}", f"провайдер {provider} не сконвертирован")
            continue
        key, target = resolve_group(group_name, groups, report)
        if not key:
            continue
        rule = {"type": "field"}
        rule.update({k: v for k, v in matcher.items() if v})
        rule[key] = target
        xray_rules.append(rule)
    return xray_rules


def inject_directive(group, prefix):
    """Группа mihomo с include-all -> Remnawave-директива injectHosts."""
    directive = {"tagPrefix": prefix}
    if group.get("filter"):
        directive["remarkRegex"] = group["filter"]
    if group.get("exclude-filter"):
        directive["excludeRemarkRegex"] = group["exclude-filter"]
    return directive


VLESS_TRANSPORT = {"raw": "raw", "tcp": "raw", "ws": "ws", "grpc": "grpc", "httpupgrade": "httpupgrade"}


def link_to_outbound(link, tag):
    """vless://uuid@host:port?params#remark -> outbound xray."""
    url = urllib.parse.urlsplit(link)
    if url.scheme != "vless":
        raise ValueError(f"поддержан только vless://, получено {url.scheme}://")
    query = dict(urllib.parse.parse_qsl(url.query))
    network = VLESS_TRANSPORT.get(query.get("type", "raw"), "raw")
    stream = {"network": network}
    if network in ("ws", "httpupgrade"):
        settings = {"path": query.get("path", "/")}
        if query.get("host"):
            settings["host" if network == "httpupgrade" else "headers"] = (
                query["host"] if network == "httpupgrade" else {"Host": query["host"]}
            )
        stream[f"{network}Settings"] = settings
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": query.get("serviceName", ""),
            "multiMode": query.get("mode") == "multi",
        }
    security = query.get("security", "none")
    stream["security"] = security
    if security == "reality":
        stream["realitySettings"] = {
            "serverName": query.get("sni", ""),
            "fingerprint": query.get("fp", "chrome"),
            "publicKey": query.get("pbk", ""),
            "shortId": query.get("sid", ""),
            "spiderX": query.get("spx", ""),
        }
    elif security == "tls":
        stream["tlsSettings"] = {
            "serverName": query.get("sni", ""),
            "fingerprint": query.get("fp", "chrome"),
            "alpn": query.get("alpn", "h2,http/1.1").split(","),
        }
    user = {"id": url.username, "encryption": query.get("encryption", "none")}
    if query.get("flow"):
        user["flow"] = query["flow"]
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {"vnext": [{"address": url.hostname, "port": url.port or 443, "users": [user]}]},
        "streamSettings": stream,
    }


def pool_for_remark(remark, groups):
    """Классифицируем ноду по её remark теми же регексами, что в mihomo-группах."""
    for group_name, (prefix, _) in POOLS.items():
        group = groups.get(group_name) or {}
        include, exclude = group.get("filter"), group.get("exclude-filter")
        if exclude and re.search(exclude, remark):
            continue
        if include and re.search(include, remark):
            return prefix
    for group_name, (prefix, _) in POOLS.items():
        group = groups.get(group_name) or {}
        if not group.get("filter") and group.get("exclude-filter"):
            if not re.search(group["exclude-filter"], remark):
                return prefix
    return None


def build_outbounds(mode, groups, links, report):
    outbounds = [
        {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIP"}},
        {"tag": "block", "protocol": "blackhole"},
    ]
    if mode == "template":
        for group_name, (prefix, _) in POOLS.items():
            group = groups.get(group_name) or {}
            outbounds.append({"remnawave": {"injectHosts": inject_directive(group, prefix)}})
        return outbounds

    counters = {}
    for link in links:
        remark = urllib.parse.unquote(urllib.parse.urlsplit(link).fragment or "")
        prefix = pool_for_remark(remark, groups)
        if not prefix:
            report.drop(f"нода {remark!r}", "не попала ни в один пул по регексам")
            continue
        counters[prefix] = counters.get(prefix, 0) + 1
        tag = prefix if counters[prefix] == 1 else f"{prefix}-{counters[prefix]}"
        outbounds.append(link_to_outbound(link, tag))
    for prefix in {p for p, _ in POOLS.values()}:
        if prefix not in counters:
            report.warn(f"пул {prefix} пуст — правила, ведущие в него, никуда не приведут")
    return outbounds


def build_config(cfg, mode, links, offline):
    report = Report()
    groups = {g["name"]: g for g in cfg.get("proxy-groups") or []}
    matchers = build_provider_matchers(cfg, report, offline)

    balancers = []
    for group_name, (prefix, strategy) in POOLS.items():
        if prefix == "foreign":
            strategy = FOREIGN_STRATEGY
        balancers.append({"tag": prefix, "selector": [prefix], "strategy": {"type": strategy}})
    for tag, (btag, selector, strategy) in AGGREGATES.items():
        balancers.append({"tag": btag, "selector": selector, "strategy": {"type": strategy}})
        report.warn(
            f"{tag}: fallback (RU-выход первым, заграница запасной) вырожден в balancer "
            f"{strategy} над {selector} — приоритет RU теряется"
        )

    config = {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": [
                {
                    "address": "https://common.dot.dns.yandex.net/dns-query",
                    "domains": ["geosite:category-ru", "geosite:ru-available-only-inside"],
                    "skipFallback": True,
                },
                "https://cloudflare-dns.com/dns-query",
            ],
            "queryStrategy": "UseIPv4",
        },
        "inbounds": [
            {
                "tag": "socks-in",
                "protocol": "socks",
                "port": 10808,
                "listen": "127.0.0.1",
                "settings": {"udp": True},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            },
            {
                "tag": "http-in",
                "protocol": "http",
                "port": 10809,
                "listen": "127.0.0.1",
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            },
        ],
        "outbounds": build_outbounds(mode, groups, links, report),
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": build_rules(cfg, matchers, groups, report),
            "balancers": balancers,
        },
        "burstObservatory": {
            "subjectSelector": sorted({p for p, _ in POOLS.values()}),
            "pingConfig": {
                "destination": "https://www.gstatic.com/generate_204",
                "connectivity": "",
                "interval": "300s",
                "sampling": 2,
                "timeout": "5s",
            },
        },
        "stats": {},
    }
    return config, report


def read_links(path):
    raw = open(path, encoding="utf-8").read().strip()
    if "://" not in raw:  # тело подписки часто отдаётся в base64
        raw = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
    return [line.strip() for line in raw.splitlines() if line.strip().startswith("vless://")]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", help="исходный mihomo YAML")
    parser.add_argument("--mode", choices=("template", "static"), default="template")
    parser.add_argument("--links", help="файл со ссылками vless:// (обязателен для --mode static)")
    parser.add_argument("--out", help="куда писать JSON (по умолчанию stdout)")
    parser.add_argument("--offline", action="store_true", help="не ходить в сеть за списками")
    args = parser.parse_args()

    if args.mode == "static" and not args.links:
        parser.error("--mode static требует --links")

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    links = read_links(args.links) if args.links else []
    config, report = build_config(cfg, args.mode, links, args.offline)

    payload = json.dumps(config, ensure_ascii=False, indent=2)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(payload + "\n")
        print(f"записано: {args.out} ({len(payload)} байт, правил: {len(config['routing']['rules'])})", file=sys.stderr)
    else:
        print(payload)
    if report.render():
        print(report.render(), file=sys.stderr)


if __name__ == "__main__":
    main()
