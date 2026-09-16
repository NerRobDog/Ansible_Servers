# Роль `warp_exit` — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WARP-выход нод Remnawave ставится, проверяется, перерегистрируется и мониторится Ansible-ролью `warp_exit`, завязанной на `remnawave.warp_mode`.

**Architecture:** Роль `roles/warp_exit` регистрирует хост через `wgcf`, собирает `warp.conf` из сгенерированного профиля, поднимает `wg-quick@warp` и fail-closed проверяет трафик до `remnawave_node`. Проба на хосте пишет метрики в textfile-коллектор node-exporter, три правила в `fleet-alerts.yml.j2` доставляются существующим Alertmanager в Telegram. Перерегистрация — отдельный путь под тегом `warp_reregister` с бэкапом и откатом.

**Tech Stack:** Ansible (ansible-core из `requirements.txt`, коллекции `ansible.builtin`, `ansible.posix`), Python 3.12 (тесты, filter-плагин, рендер fleet), Bash (проба, тесты), Prometheus 2.55.0 / `promtool`, `wgcf` v2.2.32, `wireguard-tools`.

**Spec:** [`docs/superpowers/specs/2026-09-16-warp-exit-role-design.md`](../specs/2026-09-16-warp-exit-role-design.md)

## Global Constraints

- `warp_mode` принимает ровно `none`, `all`, `inbound`; пустое или отсутствующее значение → `none`; сравнение без учёта регистра и пробелов (как `normalize_warp_mode` в `.github/scripts/remnawave-api-sync.py:220-224`).
- `wgcf` версии `2.2.32`, `linux_amd64`, SHA256 `2ff97f2201972ce582a424455d50a3719a380eef0cd1f3144f7779348e122a2c`.
- `promtool` из Prometheus `2.55.0` (та же версия, что в проде на ae-us-1), архив `prometheus-2.55.0.linux-amd64.tar.gz`, SHA256 `7a6b6d5ea003e8d59def294392c64e28338da627bf760cf268e788d6a8832a23`.
- Интерфейс называется `warp` (на это имя ссылаются профили панели: `streamSettings.sockopt.interface: warp`).
- `warp.conf`: `Table = off`, `MTU = 1280`, только IPv4 в `Address`, без `DNS`, `AllowedIPs = 0.0.0.0/0, ::/0`, `PersistentKeepalive = 25`, один завершающий перевод строки. Права 0600, владелец root.
- `wgcf-account.toml` и `wgcf-profile.conf` — права 0600.
- Ключи WireGuard никогда не попадают в вывод: `no_log: true` и `diff: false` на всех тасках, которые их читают или пишут.
- Проверка туннеля: до 5 попыток, пауза 6 секунд; успех = ненулевой handshake И `warp=on` в `https://www.cloudflare.com/cdn-cgi/trace` через `--interface warp`.
- Алерты: `WarpTunnelDown` (`warp_probe_success_ratio == 0`, for 3m), `WarpDegraded` (`avg_over_time(warp_probe_success_ratio[15m]) < 0.8`, for 1m), `WarpProbeStale` (`time() - warp_probe_last_run_timestamp_seconds > 300`, for 1m).
- Проба: 5 запросов на trace + 5 на `https://www.gstatic.com/generate_204`, параллельно, `--max-time 5`, раз в минуту.
- yamllint: `line-length` 160. ansible-lint пропускает только `var-naming`.
- Коммиты заканчиваются строкой `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## Отступления от спеки, внесённые при планировании

1. **Теги роли живут на тасках, а не на записи роли в `playbook.yml`.** Тег роли в плейбуке наследуется каждой таской. Таска с `never` пропускается, только пока не запрошен ЛЮБОЙ другой её тег — значит, при `tags: [remnawave, warp]` на записи роли обычный `--tags warp` или `--tags remnawave` запустил бы перерегистрацию и сменил выходной IP. Защищено тестом в Task 6.
2. **Скрипт пробы — статический `files/warp-probe.sh`, а не шаблон.** Параметры приходят через переменные окружения из systemd-юнита. Так скрипт тестируется напрямую, без рендера Jinja.
3. **Проверка tw-germ-1 перед реальным прогоном — по статусу таски, а не по диффу.** Шаблон `warp.conf` содержит ключи, поэтому у него `diff: false`. Критерий: в check-mode таска `Render WARP interface config` имеет статус `ok`, не `changed`.
4. **`Endpoint` берётся из профиля**, а не зашит в шаблон: сейчас он совпадает с `engage.cloudflareclient.com:2408`, но источником правды остаётся то, что выдал `wgcf`.
5. **На tw-germ-1 бинарник `wgcf` будет заменён.** Его SHA256 (`fc443008…`) не совпадает с v2.2.32. Замена файла не трогает туннель.

## Карта файлов

| Файл | Действие | Ответственность |
|---|---|---|
| `.github/scripts/render-fleet-runtime.py` | изменить | нормализация `remnawave.warp_mode` |
| `.github/scripts/test-render-fleet-runtime.py` | изменить | контракт `warp_mode` |
| `roles/warp_exit/filter_plugins/wgcf_profile.py` | создать | разбор профиля `wgcf` |
| `.github/scripts/test-warp-exit-filter.py` | создать | тест фильтра |
| `roles/warp_exit/templates/warp.conf.j2` | создать | форма `warp.conf` |
| `.github/scripts/test-warp-exit-template.py` | создать | байтовая сверка шаблона |
| `roles/warp_exit/files/warp-probe.sh` | создать | проба и запись метрик |
| `.github/scripts/test-warp-probe.sh` | создать | тест пробы с поддельными `curl`/`wg` |
| `roles/monitoring_stack/templates/fleet-alerts.yml.j2` | изменить | группа `warp-exit` |
| `.github/scripts/fixtures/warp-alerts.test.yml` | создать | сценарии `promtool test rules` |
| `.github/scripts/test-warp-alerts.sh` | создать | рендер правил + `promtool` |
| `roles/warp_exit/defaults/main.yml` | создать | параметры роли |
| `roles/warp_exit/tasks/{main,install,register,configure,verify}.yml` | создать | обычный путь |
| `roles/warp_exit/tasks/{reregister,probe}.yml` | создать | перерегистрация и развёртывание пробы |
| `roles/warp_exit/templates/warp-probe.{service,timer}.j2` | создать | systemd |
| `.github/scripts/test-warp-exit-tags.sh` | создать | защита от случайной перерегистрации |
| `roles/monitoring_agent/templates/docker-compose.yml.j2`, `roles/monitoring_agent/tasks/main.yml` | изменить | textfile-коллектор |
| `playbook.yml` | изменить | `effective_warp_mode`, подключение роли |
| `roles/node_tuning/tasks/main.yml`, `roles/node_tuning/defaults/main.yml`, `group_vars/all.yml`, `group_vars/remnawave_node.yml` | изменить | убрать запрет WARP |
| `.github/scripts/smoke-remnawave.sh` | изменить | проверка `warp=on` |
| `.github/workflows/ansible-ci.yml` | изменить | шаги новых тестов |
| `README.md`, `docs/OPERATIONS_GUIDE.md`, `docs/ROLE_CATALOG.md` | изменить | документация |

Все команды ниже выполняются из корня worktree: `/Users/nik/Documents/PycharmProjects/Ansible_Servers/.claude/worktrees/elated-curie-4f0190`, ветка `feat/warp-role`.

---

### Task 1: Валидация `warp_mode` в рендере fleet

**Files:**
- Modify: `.github/scripts/render-fleet-runtime.py:32-46` (`REMNAWAVE_DEFAULTS`), `:93` (рядом с `fail`), `:254` (после `target_profile_name`)
- Test: `.github/scripts/test-render-fleet-runtime.py` (новые функции перед `def main`, регистрация в списке `tests`)

**Interfaces:**
- Produces: в `runtime_vars.json` у каждого хоста `fleet_hosts.<alias>.remnawave.warp_mode` — всегда строка из `none|all|inbound`. Task 6 и Task 7 читают это поле.

- [ ] **Step 1: Написать падающие тесты**

Вставить перед `def main() -> int:` в `.github/scripts/test-render-fleet-runtime.py`:

```python
def render_warp_mode_host(remnawave: dict) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    config = {"hosts": {"warp-host": {"ansible_host": "203.0.113.50", "remnawave": remnawave}}}
    proc, _, vars_out, _ = run_renderer(json.dumps(config), "deploy", suffix=".json")
    if proc.returncode != 0:
        return proc, None
    runtime_vars = json.loads(vars_out.read_text(encoding="utf-8"))
    return proc, runtime_vars["fleet_hosts"]["warp-host"]["remnawave"]


def test_warp_mode_defaults_to_none() -> None:
    proc, remnawave = render_warp_mode_host({})
    assert_true(proc.returncode == 0, f"Renderer should accept a host without warp_mode: {proc.stderr}")
    assert_true(remnawave["warp_mode"] == "none", f"Missing warp_mode must render as none, got {remnawave['warp_mode']!r}")

    proc, remnawave = render_warp_mode_host({"warp_mode": ""})
    assert_true(proc.returncode == 0, f"Renderer should accept empty warp_mode: {proc.stderr}")
    assert_true(remnawave["warp_mode"] == "none", f"Empty warp_mode must render as none, got {remnawave['warp_mode']!r}")


def test_warp_mode_normalizes_case_and_whitespace() -> None:
    for raw, expected in ((" All ", "all"), ("INBOUND", "inbound"), ("none", "none")):
        proc, remnawave = render_warp_mode_host({"warp_mode": raw})
        assert_true(proc.returncode == 0, f"Renderer should accept warp_mode={raw!r}: {proc.stderr}")
        assert_true(remnawave["warp_mode"] == expected, f"warp_mode={raw!r} must render as {expected!r}, got {remnawave['warp_mode']!r}")


def test_invalid_warp_mode_rejected() -> None:
    proc, _ = render_warp_mode_host({"warp_mode": "warp"})
    output = proc.stderr + proc.stdout
    assert_true(proc.returncode != 0, "Renderer should fail for warp_mode=warp")
    assert_true("warp_mode" in output, f"Error should mention warp_mode, got: {output}")
    assert_true("warp-host" in output, f"Error should name the host, got: {output}")
```

В список `tests` внутри `main()` после `test_yusic_worker_pull_no_relay_required,` добавить:

```python
        test_warp_mode_defaults_to_none,
        test_warp_mode_normalizes_case_and_whitespace,
        test_invalid_warp_mode_rejected,
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python3 .github/scripts/test-render-fleet-runtime.py`
Expected: `FAIL: Missing warp_mode must render as none` (KeyError превратится в падение до этой строки — ожидается ненулевой код выхода с упоминанием `warp_mode`).

- [ ] **Step 3: Реализация**

В `.github/scripts/render-fleet-runtime.py` в словарь `REMNAWAVE_DEFAULTS` последним ключом добавить:

```python
    "warp_mode": "none",
```

Сразу после словаря `REMNAWAVE_DEFAULTS` добавить:

```python
WARP_MODES = ("none", "all", "inbound")
```

После функции `parse_string_list` добавить:

```python
def normalize_warp_mode(value, alias: str) -> str:
    # Mirrors normalize_warp_mode in remnawave-api-sync.py: the panel profile and the
    # warp_exit role must agree on whether a host has a WARP exit, or a typo would give
    # a node whose profile routes into a `warp` interface that nobody installed.
    mode = str(value or "").strip().lower() or "none"
    if mode not in WARP_MODES:
        fail(f"Host '{alias}' remnawave.warp_mode must be one of {', '.join(WARP_MODES)} (got {value!r}).")
    return mode
```

В `normalize_host` сразу после строки с `remnawave_cfg["target_profile_name"] = ...` добавить:

```python
    remnawave_cfg["warp_mode"] = normalize_warp_mode(remnawave_cfg.get("warp_mode"), alias)
```

- [ ] **Step 4: Тесты проходят**

Run: `python3 .github/scripts/test-render-fleet-runtime.py`
Expected: `PASS: test_warp_mode_defaults_to_none`, `PASS: test_warp_mode_normalizes_case_and_whitespace`, `PASS: test_invalid_warp_mode_rejected`, последняя строка `All render-fleet-runtime contract tests passed.`

- [ ] **Step 5: Проверить живой fleet-конфиг на совместимость**

Run:
```bash
python3 .github/scripts/render-fleet-runtime.py --fleet-config /Users/nik/Documents/PycharmProjects/Ansible_Servers/fleet.remna-yusic.deploy.yml --mode deploy --target remnawave --inventory-out /tmp/warp-inv.ini --vars-out /tmp/warp-vars.json --bootstrap-out /tmp/warp-boot.json && python3 -c "import json; h=json.load(open('/tmp/warp-vars.json'))['fleet_hosts']; print({k: v['remnawave']['warp_mode'] for k, v in h.items()})"; rm -f /tmp/warp-inv.ini /tmp/warp-vars.json /tmp/warp-boot.json
```
Expected: `dh-germ-1` → `all`, `tw-germ-1` → `inbound`, остальные → `none`. Файлы удалены (в bootstrap-файле пароли).

- [ ] **Step 6: Commit**

```bash
git add .github/scripts/render-fleet-runtime.py .github/scripts/test-render-fleet-runtime.py
git commit -m "feat(fleet): validate remnawave.warp_mode in the runtime renderer

Only remnawave-api-sync validated warp_mode, so a typo reached Ansible as an
arbitrary string. The warp_exit role keys on this field; normalize it with the
same rules as the panel sync so both sides agree on which hosts have a WARP exit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Filter-плагин `parse_wgcf_profile`

**Files:**
- Create: `roles/warp_exit/filter_plugins/wgcf_profile.py`
- Test: `.github/scripts/test-warp-exit-filter.py`

**Interfaces:**
- Produces: Jinja-фильтр `parse_wgcf_profile(text: str) -> dict` с ключами `private_key: str`, `peer_public_key: str`, `address_v4: str` (с маской, например `172.16.0.2/32`), `endpoint: str`. При нехватке поля бросает `AnsibleFilterError` с перечнем недостающих полей, **без значений ключей**. Используется в Task 3 (тест шаблона) и Task 5 (`configure.yml`).

- [ ] **Step 1: Написать падающий тест**

Create `.github/scripts/test-warp-exit-filter.py`:

```python
#!/usr/bin/env python3
"""Contract tests for the warp_exit parse_wgcf_profile filter."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN = REPO_ROOT / "roles" / "warp_exit" / "filter_plugins" / "wgcf_profile.py"

# Same layout wgcf 2.2.x writes on tw-germ-1: no blank line between sections,
# dual-stack Address, DNS line present. Keys here are fake.
PROFILE_TW_GERM_1_SHAPE = """[Interface]
PrivateKey = FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
Address = 172.16.0.2/32, 2606:4700:110:882c:3048:50b0:dac:cdfd/128
DNS = 1.1.1.1, 1.0.0.1, 2606:4700:4700::1111, 2606:4700:4700::1001
MTU = 1280
[Peer]
PublicKey = FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = engage.cloudflareclient.com:2408
"""


def load_plugin():
    spec = importlib.util.spec_from_file_location("wgcf_profile", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_error(func, text: str, must_mention: str) -> None:
    try:
        func(text)
    except Exception as exc:  # AnsibleFilterError or the local fallback class
        message = str(exc)
        assert_true(must_mention in message, f"Error should mention {must_mention!r}, got: {message}")
        assert_true("FAKE" not in message, f"Error must not leak key material, got: {message}")
        return
    raise AssertionError(f"Expected an error mentioning {must_mention!r}")


def test_parses_tw_germ_1_shape() -> None:
    parsed = load_plugin().parse_wgcf_profile(PROFILE_TW_GERM_1_SHAPE)
    assert_true(parsed == {
        "private_key": "FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
        "peer_public_key": "FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=",
        "address_v4": "172.16.0.2/32",
        "endpoint": "engage.cloudflareclient.com:2408",
    }, f"Unexpected parse result: {parsed}")


def test_keys_are_scoped_to_their_section() -> None:
    # PublicKey under [Interface] must not be taken as the peer key.
    text = PROFILE_TW_GERM_1_SHAPE.replace("[Peer]\nPublicKey = FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=\n", "[Peer]\n")
    text = text.replace("MTU = 1280\n", "MTU = 1280\nPublicKey = FAKEwrongSECTION=\n")
    expect_error(load_plugin().parse_wgcf_profile, text, "Peer.PublicKey")


def test_missing_ipv4_address_rejected() -> None:
    text = PROFILE_TW_GERM_1_SHAPE.replace("172.16.0.2/32, ", "")
    expect_error(load_plugin().parse_wgcf_profile, text, "IPv4")


def test_missing_private_key_rejected() -> None:
    text = PROFILE_TW_GERM_1_SHAPE.replace("PrivateKey = FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=\n", "")
    expect_error(load_plugin().parse_wgcf_profile, text, "Interface.PrivateKey")


def test_empty_profile_rejected() -> None:
    expect_error(load_plugin().parse_wgcf_profile, "   \n", "empty")


def test_registered_as_ansible_filter() -> None:
    filters = load_plugin().FilterModule().filters()
    assert_true("parse_wgcf_profile" in filters, f"Filter not registered: {sorted(filters)}")


def main() -> int:
    tests = [
        test_parses_tw_germ_1_shape,
        test_keys_are_scoped_to_their_section,
        test_missing_ipv4_address_rejected,
        test_missing_private_key_rejected,
        test_empty_profile_rejected,
        test_registered_as_ansible_filter,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All warp_exit filter contract tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python3 .github/scripts/test-warp-exit-filter.py`
Expected: `FileNotFoundError` на `roles/warp_exit/filter_plugins/wgcf_profile.py`, ненулевой код выхода.

- [ ] **Step 3: Реализация**

Create `roles/warp_exit/filter_plugins/wgcf_profile.py`:

```python
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
```

- [ ] **Step 4: Тест проходит**

Run: `python3 .github/scripts/test-warp-exit-filter.py`
Expected: 6 строк `PASS:`, затем `All warp_exit filter contract tests passed.`

- [ ] **Step 5: Добавить шаг в CI**

В `.github/workflows/ansible-ci.yml` после шага `mihomo routing contract tests` добавить:

```yaml
      - name: warp_exit filter contract tests
        run: python .github/scripts/test-warp-exit-filter.py
```

- [ ] **Step 6: Commit**

```bash
git add roles/warp_exit/filter_plugins/wgcf_profile.py .github/scripts/test-warp-exit-filter.py .github/workflows/ansible-ci.yml
git commit -m "feat(warp_exit): parse wgcf profiles in a tested filter plugin

warp.conf is built from the profile wgcf generates. Parsing lives in a filter
plugin so it can be unit-tested; keys are scoped to their section, IPv6 and DNS
are ignored, and errors name missing fields without echoing key material.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Шаблон `warp.conf`

**Files:**
- Create: `roles/warp_exit/templates/warp.conf.j2`
- Test: `.github/scripts/test-warp-exit-template.py`

**Interfaces:**
- Consumes: `parse_wgcf_profile` из Task 2.
- Produces: шаблон ожидает переменные `warp_exit_profile` (результат фильтра), `warp_exit_mtu` (int), `warp_exit_keepalive` (int). Task 5 рендерит его в `/etc/wireguard/warp.conf`.

- [ ] **Step 1: Написать падающий тест**

Create `.github/scripts/test-warp-exit-template.py`:

```python
#!/usr/bin/env python3
"""Byte-for-byte contract test for roles/warp_exit/templates/warp.conf.j2.

The expected text reproduces /etc/wireguard/warp.conf on tw-germ-1 (2026-09-16)
with its keys replaced. Any drift here means the first role run on tw-germ-1
reports a change and restarts its live WARP tunnel.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import jinja2

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "roles" / "warp_exit" / "templates" / "warp.conf.j2"
PLUGIN = REPO_ROOT / "roles" / "warp_exit" / "filter_plugins" / "wgcf_profile.py"

PROFILE = """[Interface]
PrivateKey = FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
Address = 172.16.0.2/32, 2606:4700:110:882c:3048:50b0:dac:cdfd/128
DNS = 1.1.1.1, 1.0.0.1, 2606:4700:4700::1111, 2606:4700:4700::1001
MTU = 1280
[Peer]
PublicKey = FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = engage.cloudflareclient.com:2408
"""

EXPECTED = (
    "[Interface]\n"
    "PrivateKey = FAKEprivateKEYaaaaaaaaaaaaaaaaaaaaaaaaaaaa=\n"
    "Address = 172.16.0.2/32\n"
    "MTU = 1280\n"
    "Table = off\n"
    "\n"
    "[Peer]\n"
    "PublicKey = FAKEpeerKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=\n"
    "AllowedIPs = 0.0.0.0/0, ::/0\n"
    "Endpoint = engage.cloudflareclient.com:2408\n"
    "PersistentKeepalive = 25\n"
)


def load_filter():
    spec = importlib.util.spec_from_file_location("wgcf_profile", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_wgcf_profile


def render(**variables) -> str:
    # Ansible's template module renders with trim_blocks=True and keeps the trailing newline.
    env = jinja2.Environment(trim_blocks=True, keep_trailing_newline=True, undefined=jinja2.StrictUndefined)
    return env.from_string(TEMPLATE.read_text(encoding="utf-8")).render(**variables)


def main() -> int:
    rendered = render(warp_exit_profile=load_filter()(PROFILE), warp_exit_mtu=1280, warp_exit_keepalive=25)
    if rendered != EXPECTED:
        print("FAIL: rendered warp.conf differs from the tw-germ-1 shape", file=sys.stderr)
        print("--- expected", file=sys.stderr)
        print(repr(EXPECTED), file=sys.stderr)
        print("--- rendered", file=sys.stderr)
        print(repr(rendered), file=sys.stderr)
        return 1
    print("PASS: warp.conf.j2 matches the tw-germ-1 shape byte for byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python3 .github/scripts/test-warp-exit-template.py`
Expected: `FileNotFoundError` на `warp.conf.j2`.

- [ ] **Step 3: Реализация**

Create `roles/warp_exit/templates/warp.conf.j2` (без заголовка `ansible_managed`: файл должен совпадать с живым байт в байт; ровно один перевод строки в конце):

```
[Interface]
PrivateKey = {{ warp_exit_profile.private_key }}
Address = {{ warp_exit_profile.address_v4 }}
MTU = {{ warp_exit_mtu }}
Table = off

[Peer]
PublicKey = {{ warp_exit_profile.peer_public_key }}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = {{ warp_exit_profile.endpoint }}
PersistentKeepalive = {{ warp_exit_keepalive }}
```

- [ ] **Step 4: Тест проходит**

Run: `python3 .github/scripts/test-warp-exit-template.py`
Expected: `PASS: warp.conf.j2 matches the tw-germ-1 shape byte for byte`

- [ ] **Step 5: Сверка с живым файлом на tw-germ-1 (только чтение, ключи не выводятся)**

Run:
```bash
ssh -i ~/.ssh/ansible_actions -o BatchMode=yes deploy@5.42.127.98 'sudo -n sed -E "s/^(PrivateKey|PublicKey) = .*/\1 = KEY/" /etc/wireguard/warp.conf | sha256sum'
printf '[Interface]\nPrivateKey = KEY\nAddress = 172.16.0.2/32\nMTU = 1280\nTable = off\n\n[Peer]\nPublicKey = KEY\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = engage.cloudflareclient.com:2408\nPersistentKeepalive = 25\n' | shasum -a 256
```
Expected: два одинаковых хэша. Если они разные — остановиться и поправить шаблон и `EXPECTED` под живой файл, не хост.

- [ ] **Step 6: Добавить шаг в CI**

В `.github/workflows/ansible-ci.yml` после шага `warp_exit filter contract tests`:

```yaml
      - name: warp_exit template contract test
        run: python .github/scripts/test-warp-exit-template.py
```

- [ ] **Step 7: Commit**

```bash
git add roles/warp_exit/templates/warp.conf.j2 .github/scripts/test-warp-exit-template.py .github/workflows/ansible-ci.yml
git commit -m "feat(warp_exit): warp.conf template matching the live tw-germ-1 file

Table = off keeps the tunnel out of host routing so only Xray traffic bound to
the warp interface uses it. The template is checked byte for byte against the
live file shape, so adopting tw-germ-1 does not restart its tunnel.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Скрипт пробы WARP

**Files:**
- Create: `roles/warp_exit/files/warp-probe.sh`
- Test: `.github/scripts/test-warp-probe.sh`

**Interfaces:**
- Produces: исполняемый скрипт. Параметры через окружение: `WARP_PROBE_IFACE` (default `warp`), `WARP_PROBE_OUT` (default `/var/lib/node_exporter/textfile/warp.prom`), `WARP_PROBE_CURL` (default `curl`), `WARP_PROBE_WG` (default `wg`), `WARP_PROBE_TRACE_URL`, `WARP_PROBE_204_URL`, `WARP_PROBE_PER_TARGET` (default `5`), `WARP_PROBE_TIMEOUT` (default `5`), `WARP_PROBE_NOW` (только для тестов). Пишет метрики `warp_probe_success_ratio`, `warp_probe_latency_avg_seconds`, `warp_status_on`, `warp_handshake_age_seconds`, `warp_probe_last_run_timestamp_seconds`. Task 5 алертит по этим именам, Task 6 разворачивает скрипт.

- [ ] **Step 1: Написать падающий тест**

Create `.github/scripts/test-warp-probe.sh`:

```bash
#!/usr/bin/env bash
# Contract tests for roles/warp_exit/files/warp-probe.sh using fake curl and wg binaries.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
probe="$repo_root/roles/warp_exit/files/warp-probe.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/bin"
cat > "$work/bin/curl" <<'FAKE'
#!/usr/bin/env bash
# Fake curl: honours --output and the URL, answers according to FAKE_CURL_MODE.
out="" url=""
while (($#)); do
  case "$1" in
    --output) out="$2"; shift 2 ;;
    --interface|--max-time|--write-out) shift 2 ;;
    --silent) shift ;;
    *) url="$1"; shift ;;
  esac
done
case "$url" in
  *cdn-cgi/trace*) kind=trace ;;
  *) kind=g204 ;;
esac
case "${FAKE_CURL_MODE}:${kind}" in
  ok:trace|half:trace) printf 'fl=1\nwarp=on\n' > "$out"; printf '200 0.100000' ;;
  nowarp:trace) printf 'fl=1\nwarp=off\n' > "$out"; printf '200 0.100000' ;;
  ok:g204|nowarp:g204) : > "$out"; printf '204 0.050000' ;;
  *) printf '000 5.000000'; exit 28 ;;
esac
FAKE
cat > "$work/bin/wg" <<'FAKE'
#!/usr/bin/env bash
printf 'FAKEpeer=\t%s\n' "${FAKE_WG_HANDSHAKE}"
FAKE
chmod +x "$work/bin/curl" "$work/bin/wg"

fail() { echo "FAIL: $*" >&2; exit 1; }

run_case() {
  local mode="$1" handshake="$2" dir="$work/case-$1-$2"
  mkdir -p "$dir"
  FAKE_CURL_MODE="$mode" FAKE_WG_HANDSHAKE="$handshake" \
    WARP_PROBE_CURL="$work/bin/curl" WARP_PROBE_WG="$work/bin/wg" \
    WARP_PROBE_OUT="$dir/warp.prom" WARP_PROBE_NOW=1060 \
    bash "$probe"
  echo "$dir"
}

metric() { awk -v name="$1" '$1 == name { print $2 }' "$2/warp.prom"; }

expect_num() {
  local name="$1" dir="$2" want="$3" got
  got="$(metric "$name" "$dir")"
  [[ -n "$got" ]] || fail "$name missing in $dir/warp.prom"
  awk -v g="$got" -v w="$want" 'BEGIN { d = g - w; if (d < 0) d = -d; exit !(d < 0.000001) }' \
    || fail "$name = $got, want $want ($dir)"
}

expect_nan() {
  local got
  got="$(metric "$1" "$2")"
  [[ "$got" == "NaN" ]] || fail "$1 = '$got', want NaN ($2)"
}

check_format() {
  local dir="$1" extra
  grep -vE '^# (HELP|TYPE) warp_[a-z_]+ .+$|^warp_[a-z_]+ (NaN|[0-9]+(\.[0-9]+)?)$' "$dir/warp.prom" \
    && fail "unexpected lines in $dir/warp.prom"
  extra="$(find "$dir" -type f ! -name warp.prom | wc -l | tr -d ' ')"
  [[ "$extra" == 0 ]] || fail "temporary files left next to warp.prom in $dir"
}

dir="$(run_case ok 1000)"
expect_num warp_probe_success_ratio "$dir" 1
expect_num warp_probe_latency_avg_seconds "$dir" 0.075
expect_num warp_status_on "$dir" 1
expect_num warp_handshake_age_seconds "$dir" 60
expect_num warp_probe_last_run_timestamp_seconds "$dir" 1060
check_format "$dir"
echo "PASS: all requests succeed"

dir="$(run_case half 1000)"
expect_num warp_probe_success_ratio "$dir" 0.5
expect_num warp_probe_latency_avg_seconds "$dir" 0.1
expect_num warp_status_on "$dir" 1
check_format "$dir"
echo "PASS: half of the requests hang"

dir="$(run_case down 0)"
expect_num warp_probe_success_ratio "$dir" 0
expect_nan warp_probe_latency_avg_seconds "$dir"
expect_num warp_status_on "$dir" 0
expect_nan warp_handshake_age_seconds "$dir"
check_format "$dir"
echo "PASS: tunnel down, no handshake"

dir="$(run_case nowarp 1000)"
expect_num warp_probe_success_ratio "$dir" 0.5
expect_num warp_status_on "$dir" 0
check_format "$dir"
echo "PASS: trace without warp=on is not counted as success"

echo "All warp-probe contract tests passed."
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `bash .github/scripts/test-warp-probe.sh`
Expected: ошибка `No such file or directory` на `warp-probe.sh`, ненулевой код выхода.

- [ ] **Step 3: Реализация**

Create `roles/warp_exit/files/warp-probe.sh`:

```bash
#!/usr/bin/env bash
# Sends real requests through the WARP interface and publishes the result for
# node-exporter's textfile collector. A fresh handshake proves nothing on its own:
# a degraded free registration keeps handshaking while most tunneled requests hang.
set -uo pipefail

iface="${WARP_PROBE_IFACE:-warp}"
out="${WARP_PROBE_OUT:-/var/lib/node_exporter/textfile/warp.prom}"
curl_bin="${WARP_PROBE_CURL:-curl}"
wg_bin="${WARP_PROBE_WG:-wg}"
trace_url="${WARP_PROBE_TRACE_URL:-https://www.cloudflare.com/cdn-cgi/trace}"
g204_url="${WARP_PROBE_204_URL:-https://www.gstatic.com/generate_204}"
per_target="${WARP_PROBE_PER_TARGET:-5}"
timeout_s="${WARP_PROBE_TIMEOUT:-5}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

probe() {
  local kind="$1" url="$2" n="$3" meta
  meta="$("$curl_bin" --interface "$iface" --silent --max-time "$timeout_s" \
    --output "$work/$kind.$n.body" --write-out '%{http_code} %{time_total}' "$url" 2>/dev/null)" || meta="000 0"
  printf '%s\n' "$meta" > "$work/$kind.$n.meta"
}

for n in $(seq 1 "$per_target"); do
  probe trace "$trace_url" "$n" &
  probe g204 "$g204_url" "$n" &
done
wait

total=0
ok=0
warp_on=0
latency_sum=0
for meta in "$work"/*.meta; do
  total=$((total + 1))
  read -r code seconds < "$meta"
  kind="$(basename "$meta")"
  kind="${kind%%.*}"
  success=0
  if [[ "$kind" == trace && "$code" == 200 ]] && grep -qx 'warp=on' "${meta%.meta}.body" 2>/dev/null; then
    success=1
    warp_on=1
  elif [[ "$kind" == g204 && "$code" == 204 ]]; then
    success=1
  fi
  if ((success)); then
    ok=$((ok + 1))
    latency_sum="$(awk -v a="$latency_sum" -v b="$seconds" 'BEGIN { printf "%.6f", a + b }')"
  fi
done

ratio="$(awk -v o="$ok" -v t="$total" 'BEGIN { if (t == 0) print "0"; else printf "%.6f", o / t }')"
if ((ok > 0)); then
  latency="$(awk -v s="$latency_sum" -v o="$ok" 'BEGIN { printf "%.6f", s / o }')"
else
  latency="NaN"
fi

now="${WARP_PROBE_NOW:-$(date +%s)}"
handshake="$("$wg_bin" show "$iface" latest-handshakes 2>/dev/null | awk 'NR == 1 { print $2 }')"
if [[ "$handshake" =~ ^[0-9]+$ ]] && ((handshake > 0)); then
  handshake_age=$((now - handshake))
else
  handshake_age="NaN"
fi

# Write next to the target and rename: node-exporter must never read a half-written
# file, and it only collects *.prom, so the temporary name is ignored.
tmp="$(mktemp "${out}.XXXXXX")"
{
  echo "# HELP warp_probe_success_ratio Share of probe requests through the WARP interface that succeeded in the last run."
  echo "# TYPE warp_probe_success_ratio gauge"
  echo "warp_probe_success_ratio $ratio"
  echo "# HELP warp_probe_latency_avg_seconds Mean duration of successful probe requests in the last run."
  echo "# TYPE warp_probe_latency_avg_seconds gauge"
  echo "warp_probe_latency_avg_seconds $latency"
  echo "# HELP warp_status_on Whether Cloudflare reported warp=on for traffic from this interface."
  echo "# TYPE warp_status_on gauge"
  echo "warp_status_on $warp_on"
  echo "# HELP warp_handshake_age_seconds Seconds since the last WireGuard handshake with the WARP peer."
  echo "# TYPE warp_handshake_age_seconds gauge"
  echo "warp_handshake_age_seconds $handshake_age"
  echo "# HELP warp_probe_last_run_timestamp_seconds Unix time the probe last finished."
  echo "# TYPE warp_probe_last_run_timestamp_seconds gauge"
  echo "warp_probe_last_run_timestamp_seconds $now"
} > "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$out"
```

- [ ] **Step 4: Тест проходит**

Run: `bash .github/scripts/test-warp-probe.sh`
Expected: 4 строки `PASS:`, затем `All warp-probe contract tests passed.`

- [ ] **Step 5: Добавить шаг в CI**

В `.github/workflows/ansible-ci.yml` после шага `warp_exit template contract test`:

```yaml
      - name: warp-probe contract tests
        run: bash .github/scripts/test-warp-probe.sh
```

- [ ] **Step 6: Commit**

```bash
git add roles/warp_exit/files/warp-probe.sh .github/scripts/test-warp-probe.sh .github/workflows/ansible-ci.yml
git commit -m "feat(warp_exit): probe real traffic through the WARP tunnel

A degraded free WARP registration keeps a fresh handshake while most tunneled
requests hang, so the probe sends ten parallel requests through the interface
and publishes the success ratio, latency, warp=on and handshake age for
node-exporter's textfile collector.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Правила алертов `warp-exit`

**Files:**
- Modify: `roles/monitoring_stack/templates/fleet-alerts.yml.j2` (новая группа в конец файла)
- Create: `.github/scripts/fixtures/warp-alerts.test.yml`, `.github/scripts/test-warp-alerts.sh`

**Interfaces:**
- Consumes: имена метрик из Task 4.
- Produces: алерты `WarpTunnelDown`, `WarpDegraded`, `WarpProbeStale` с лейблами `severity` и `scope: warp`.

- [ ] **Step 1: Написать сценарии `promtool` (падающий тест)**

Create `.github/scripts/fixtures/warp-alerts.test.yml`:

```yaml
---
rule_files:
  - fleet-alerts.yml

evaluation_interval: 1m

tests:
  # Healthy exit: nothing fires.
  - interval: 1m
    input_series:
      - series: 'warp_probe_success_ratio{instance="warp-host:9100",job="monitoring-node-exporter"}'
        values: '1x30'
      - series: 'warp_probe_last_run_timestamp_seconds{instance="warp-host:9100",job="monitoring-node-exporter"}'
        values: '0+60x30'
    alert_rule_test:
      - eval_time: 25m
        alertname: WarpTunnelDown
        exp_alerts: []
      - eval_time: 25m
        alertname: WarpDegraded
        exp_alerts: []
      - eval_time: 25m
        alertname: WarpProbeStale
        exp_alerts: []

  # A single bad minute (a 20-30 s burst) must not page anyone.
  - interval: 1m
    input_series:
      - series: 'warp_probe_success_ratio{instance="warp-host:9100",job="monitoring-node-exporter"}'
        values: '1x10 0 1x20'
    alert_rule_test:
      - eval_time: 12m
        alertname: WarpTunnelDown
        exp_alerts: []
      - eval_time: 25m
        alertname: WarpDegraded
        exp_alerts: []

  # Sustained 50% loss: degradation fires, the tunnel is not "down".
  - interval: 1m
    input_series:
      - series: 'warp_probe_success_ratio{instance="warp-host:9100",job="monitoring-node-exporter"}'
        values: '0.5x25'
    alert_rule_test:
      - eval_time: 20m
        alertname: WarpDegraded
        exp_alerts:
          - exp_labels:
              severity: warning
              scope: warp
              instance: warp-host:9100
              job: monitoring-node-exporter
            exp_annotations:
              summary: "WARP registration is degrading on warp-host:9100"
              description: >-
                More than 20% of probe requests through WARP on warp-host:9100 failed over the last 15 minutes.
                Re-register: run deploy-remnawave-node with tags=warp_reregister and limit set to this host.
      - eval_time: 20m
        alertname: WarpTunnelDown
        exp_alerts: []

  # Total loss: two minutes is still a restart, three minutes is an outage.
  - interval: 1m
    input_series:
      - series: 'warp_probe_success_ratio{instance="warp-host:9100",job="monitoring-node-exporter"}'
        values: '1x5 0x10'
    alert_rule_test:
      - eval_time: 8m
        alertname: WarpTunnelDown
        exp_alerts: []
      - eval_time: 10m
        alertname: WarpTunnelDown
        exp_alerts:
          - exp_labels:
              severity: critical
              scope: warp
              instance: warp-host:9100
              job: monitoring-node-exporter
            exp_annotations:
              summary: "WARP exit is down on warp-host:9100"
              description: >-
                Every probe request through the WARP interface on warp-host:9100 has failed for 3 minutes.
                Clients routed to WARP on this node have no exit.

  # node-exporter re-reads the last file forever, so a dead timer shows up as a frozen timestamp.
  - interval: 1m
    input_series:
      - series: 'warp_probe_last_run_timestamp_seconds{instance="warp-host:9100",job="monitoring-node-exporter"}'
        values: '0+60x10 600x10'
    alert_rule_test:
      - eval_time: 14m
        alertname: WarpProbeStale
        exp_alerts: []
      - eval_time: 17m
        alertname: WarpProbeStale
        exp_alerts:
          - exp_labels:
              severity: warning
              scope: warp
              instance: warp-host:9100
              job: monitoring-node-exporter
            exp_annotations:
              summary: "WARP probe stopped reporting on warp-host:9100"
              description: >-
                warp-probe.timer on warp-host:9100 has not written results for over 5 minutes.
                WARP health on this node is unknown, and WarpTunnelDown and WarpDegraded cannot fire.
```

Create `.github/scripts/test-warp-alerts.sh`:

```bash
#!/usr/bin/env bash
# Renders fleet-alerts.yml.j2 and unit-tests the warp-exit rules with promtool.
# PROMTOOL may be a command line, e.g. a docker run wrapper when promtool is not installed locally.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
read -r -a promtool_cmd <<< "${PROMTOOL:-promtool}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

python3 - "$repo_root/roles/monitoring_stack/templates/fleet-alerts.yml.j2" "$work/fleet-alerts.yml" <<'PY'
import sys

import jinja2

source, target = sys.argv[1], sys.argv[2]
env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
with open(source, encoding="utf-8") as handle:
    rendered = env.from_string(handle.read()).render()
with open(target, "w", encoding="utf-8") as handle:
    handle.write(rendered)
PY

cp "$repo_root/.github/scripts/fixtures/warp-alerts.test.yml" "$work/warp-alerts.test.yml"
"${promtool_cmd[@]}" check rules "$work/fleet-alerts.yml"
"${promtool_cmd[@]}" test rules "$work/warp-alerts.test.yml"
echo "All warp-exit alert rule tests passed."
```

- [ ] **Step 2: Убедиться, что тест падает**

Run (локально promtool через docker; на macOS временный каталог должен быть под `/tmp`, чтобы его увидел контейнер):
```bash
TMPDIR=/tmp PROMTOOL="docker run --rm -v /tmp:/tmp --entrypoint promtool prom/prometheus:v2.55.0" bash .github/scripts/test-warp-alerts.sh
```
Expected: `promtool test rules` падает — в выводе `alertname: WarpDegraded` / `WarpTunnelDown` / `WarpProbeStale` с `got:[]` против ожидаемых алертов.

- [ ] **Step 3: Реализация**

В конец `roles/monitoring_stack/templates/fleet-alerts.yml.j2` добавить (отступы как у существующих групп):

```yaml

  - name: warp-exit
    rules:
      - alert: WarpTunnelDown
        expr: warp_probe_success_ratio == 0
        for: 3m
        labels:
          severity: critical
          scope: warp
        annotations:
          summary: "WARP exit is down on {% raw %}{{ $labels.instance }}{% endraw %}"
          description: >-
            Every probe request through the WARP interface on {% raw %}{{ $labels.instance }}{% endraw %} has failed for 3 minutes.
            Clients routed to WARP on this node have no exit.

      # 20% over 15 minutes catches a wearing-out free registration long before the 60% loss seen
      # in July 2026, while a 20-30 s burst cannot move a 15-minute average past the threshold.
      - alert: WarpDegraded
        expr: avg_over_time(warp_probe_success_ratio[15m]) < 0.8
        for: 1m
        labels:
          severity: warning
          scope: warp
        annotations:
          summary: "WARP registration is degrading on {% raw %}{{ $labels.instance }}{% endraw %}"
          description: >-
            More than 20% of probe requests through WARP on {% raw %}{{ $labels.instance }}{% endraw %} failed over the last 15 minutes.
            Re-register: run deploy-remnawave-node with tags=warp_reregister and limit set to this host.

      # Without this a dead probe timer looks like a healthy exit: the ratio simply stops changing.
      - alert: WarpProbeStale
        expr: time() - warp_probe_last_run_timestamp_seconds > 300
        for: 1m
        labels:
          severity: warning
          scope: warp
        annotations:
          summary: "WARP probe stopped reporting on {% raw %}{{ $labels.instance }}{% endraw %}"
          description: >-
            warp-probe.timer on {% raw %}{{ $labels.instance }}{% endraw %} has not written results for over 5 minutes.
            WARP health on this node is unknown, and WarpTunnelDown and WarpDegraded cannot fire.
```

- [ ] **Step 4: Тест проходит**

Run:
```bash
TMPDIR=/tmp PROMTOOL="docker run --rm -v /tmp:/tmp --entrypoint promtool prom/prometheus:v2.55.0" bash .github/scripts/test-warp-alerts.sh
```
Expected: `SUCCESS` от `check rules`, `SUCCESS` от `test rules`, затем `All warp-exit alert rule tests passed.`

Если конкретный `eval_time` не совпал на границе окна — сдвигать `eval_time` в сценарии, а не пороги в правиле: пороги согласованы в спеке.

- [ ] **Step 5: Добавить шаги в CI**

В `.github/workflows/ansible-ci.yml` после шага `warp-probe contract tests`:

```yaml
      - name: Install promtool 2.55.0
        run: |
          curl -fsSL -o /tmp/prometheus.tar.gz \
            https://github.com/prometheus/prometheus/releases/download/v2.55.0/prometheus-2.55.0.linux-amd64.tar.gz
          echo "7a6b6d5ea003e8d59def294392c64e28338da627bf760cf268e788d6a8832a23  /tmp/prometheus.tar.gz" | sha256sum -c -
          tar -xzf /tmp/prometheus.tar.gz -C /tmp prometheus-2.55.0.linux-amd64/promtool
          sudo install -m 0755 /tmp/prometheus-2.55.0.linux-amd64/promtool /usr/local/bin/promtool

      - name: warp-exit alert rule tests
        run: bash .github/scripts/test-warp-alerts.sh
```

- [ ] **Step 6: yamllint по изменённым файлам**

Run: `yamllint roles/monitoring_stack/templates/fleet-alerts.yml.j2 .github/scripts/fixtures/warp-alerts.test.yml .github/workflows/ansible-ci.yml`
Expected: без ошибок (`.j2` yamllint по умолчанию не проверяет — это нормально; фикстура и workflow должны пройти).

- [ ] **Step 7: Commit**

```bash
git add roles/monitoring_stack/templates/fleet-alerts.yml.j2 .github/scripts/fixtures/warp-alerts.test.yml .github/scripts/test-warp-alerts.sh .github/workflows/ansible-ci.yml
git commit -m "feat(monitoring): alert on WARP outage, degradation and a stale probe

WarpTunnelDown pages on three minutes of total loss, WarpDegraded on more than
20% loss over 15 minutes, and WarpProbeStale on a frozen probe timestamp, which
would otherwise make a dead timer look like a healthy exit. The thresholds are
pinned by promtool unit tests against Prometheus 2.55.0, the production version.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Роль `warp_exit` и подключение к плейбуку

**Files:**
- Create: `roles/warp_exit/defaults/main.yml`, `roles/warp_exit/tasks/main.yml`, `install.yml`, `register.yml`, `configure.yml`, `verify.yml`, `reregister.yml`, `probe.yml`, `roles/warp_exit/templates/warp-probe.service.j2`, `roles/warp_exit/templates/warp-probe.timer.j2`
- Modify: `playbook.yml` (факт в `Resolve effective feature flags`, запись роли между `monitoring_stack` и `remnawave_node`), `roles/monitoring_agent/templates/docker-compose.yml.j2:11-12`, `roles/monitoring_agent/tasks/main.yml` (после `Ensure monitoring agent install directory exists`), `roles/node_tuning/tasks/main.yml:17-22`, `roles/node_tuning/defaults/main.yml:4`, `group_vars/all.yml:98`, `group_vars/remnawave_node.yml:22`
- Test: `.github/scripts/test-warp-exit-tags.sh`

**Interfaces:**
- Consumes: `fleet_hosts.<alias>.remnawave.warp_mode` (Task 1), `parse_wgcf_profile` (Task 2), `warp.conf.j2` (Task 3), `files/warp-probe.sh` (Task 4).
- Produces: факт `effective_warp_mode` в `playbook.yml`; systemd-юниты `wg-quick@warp.service`, `warp-probe.service`, `warp-probe.timer`; файл `/var/lib/node_exporter/textfile/warp.prom`; тег `warp_reregister`.

- [ ] **Step 1: Написать падающий тест на выбор тегов**

Create `.github/scripts/test-warp-exit-tags.sh`:

```bash
#!/usr/bin/env bash
# Guards the one mistake that would silently rotate a WARP exit IP: re-registration must
# run only under --tags warp_reregister, never under the tags a normal deploy uses.
# A task tagged `never` still runs when ANY of its other tags is requested, so a role-level
# tag in playbook.yml inherited by the re-registration tasks would break this.
set -euo pipefail

cd "$(dirname "$0")/../.."

reregister_marker="Back up the current WARP registration"
verify_marker="Verify traffic leaves through WARP"

list_tasks() {
  if [[ -n "$1" ]]; then
    ansible-playbook -i hosts.example.ini playbook.yml --list-tasks --tags "$1"
  else
    ansible-playbook -i hosts.example.ini playbook.yml --list-tasks
  fi
}

fail() { echo "FAIL: $*" >&2; exit 1; }

for tags in "" warp remnawave monitoring "warp,monitoring" "remnawave,node"; do
  listing="$(list_tasks "$tags")"
  if grep -qF "$reregister_marker" <<< "$listing"; then
    fail "--tags '${tags:-<none>}' would run WARP re-registration"
  fi
  echo "PASS: --tags '${tags:-<none>}' does not include re-registration"
done

listing="$(list_tasks warp)"
grep -qF "$verify_marker" <<< "$listing" || fail "--tags warp does not include the tunnel verification"
echo "PASS: --tags warp includes the tunnel verification"

listing="$(list_tasks warp_reregister)"
grep -qF "$reregister_marker" <<< "$listing" || fail "--tags warp_reregister does not include re-registration"
grep -qF "Install WireGuard tools" <<< "$listing" && fail "--tags warp_reregister must not run the install path"
echo "PASS: --tags warp_reregister runs re-registration only"

echo "All warp_exit tag selection tests passed."
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `bash .github/scripts/test-warp-exit-tags.sh`
Expected: `FAIL: --tags warp does not include the tunnel verification` (роли ещё нет).

- [ ] **Step 3: `defaults/main.yml`**

Create `roles/warp_exit/defaults/main.yml`:

```yaml
---
warp_exit_wgcf_version: "2.2.32"
warp_exit_wgcf_sha256: "2ff97f2201972ce582a424455d50a3719a380eef0cd1f3144f7779348e122a2c"
warp_exit_wgcf_url: "https://github.com/ViRb3/wgcf/releases/download/v{{ warp_exit_wgcf_version }}/wgcf_{{ warp_exit_wgcf_version }}_linux_amd64"
warp_exit_wgcf_path: /usr/local/bin/wgcf

warp_exit_dir: /etc/wireguard
# Remnawave profiles bind the WARP outbound to this interface name (sockopt.interface: warp).
warp_exit_interface: warp
warp_exit_mtu: 1280
warp_exit_keepalive: 25

warp_exit_verify_retries: 5
warp_exit_verify_delay: 6
warp_exit_trace_url: https://www.cloudflare.com/cdn-cgi/trace

warp_exit_probe_dir: /usr/local/lib/warp-exit
warp_exit_probe_script: "{{ warp_exit_probe_dir }}/warp-probe.sh"
warp_exit_textfile_dir: /var/lib/node_exporter/textfile
```

- [ ] **Step 4: `tasks/main.yml`**

Create `roles/warp_exit/tasks/main.yml`:

```yaml
---
# Tags are set here, per import, and deliberately NOT on the warp_exit entry in playbook.yml.
# A role-level tag is inherited by every task, and a task tagged `never` still runs when ANY
# of its other tags is requested: `--tags warp` would then re-register WARP and change the
# exit IP. .github/scripts/test-warp-exit-tags.sh guards this.
- name: Install WARP tooling
  ansible.builtin.import_tasks: install.yml
  tags: [remnawave, warp]

- name: Register WARP when the host has no registration
  ansible.builtin.import_tasks: register.yml
  tags: [remnawave, warp]

- name: Configure the WARP interface
  ansible.builtin.import_tasks: configure.yml
  tags: [remnawave, warp]

- name: Verify the WARP exit before the node is deployed
  ansible.builtin.import_tasks: verify.yml
  tags: [remnawave, warp]

- name: Deploy the WARP probe
  ansible.builtin.import_tasks: probe.yml
  tags: [remnawave, warp, monitoring]

- name: Re-register WARP on explicit request
  ansible.builtin.import_tasks: reregister.yml
  tags: [never, warp_reregister]
```

- [ ] **Step 5: `tasks/install.yml`**

Create `roles/warp_exit/tasks/install.yml`:

```yaml
---
- name: Validate WARP exit prerequisites
  ansible.builtin.assert:
    that:
      - ansible_os_family == "Debian"
      - ansible_architecture == "x86_64"
    fail_msg: "Role warp_exit supports Debian/Ubuntu on x86_64 only: wgcf is pinned to the linux_amd64 build."

- name: Install WireGuard tools
  ansible.builtin.apt:
    name: wireguard-tools
    state: present
    update_cache: true
    cache_valid_time: 3600

# get_url compares the checksum of an existing file and replaces it only on mismatch,
# so a hand-installed wgcf of another version is swapped once and then left alone.
- name: Install pinned wgcf binary
  ansible.builtin.get_url:
    url: "{{ warp_exit_wgcf_url }}"
    dest: "{{ warp_exit_wgcf_path }}"
    checksum: "sha256:{{ warp_exit_wgcf_sha256 }}"
    owner: root
    group: root
    mode: "0755"
```

- [ ] **Step 6: `tasks/register.yml`**

Create `roles/warp_exit/tasks/register.yml`:

```yaml
---
- name: Ensure WireGuard config directory
  ansible.builtin.file:
    path: "{{ warp_exit_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0700"

- name: Check for an existing WARP registration
  ansible.builtin.stat:
    path: "{{ warp_exit_dir }}/wgcf-account.toml"
  register: warp_exit_account

# `creates` makes both commands no-ops when the files exist and skips them in check mode,
# so a check run never calls the Cloudflare API.
- name: Register a new WARP account
  ansible.builtin.command:
    cmd: "{{ warp_exit_wgcf_path }} register --accept-tos"
    chdir: "{{ warp_exit_dir }}"
    creates: "{{ warp_exit_dir }}/wgcf-account.toml"
  no_log: true

- name: Generate the WireGuard profile for the WARP account
  ansible.builtin.command:
    cmd: "{{ warp_exit_wgcf_path }} generate"
    chdir: "{{ warp_exit_dir }}"
    creates: "{{ warp_exit_dir }}/wgcf-profile.conf"
  no_log: true

- name: Restrict WARP registration files to root
  ansible.builtin.file:
    path: "{{ warp_exit_dir }}/{{ item }}"
    owner: root
    group: root
    mode: "0600"
  loop:
    - wgcf-account.toml
    - wgcf-profile.conf
  when: warp_exit_account.stat.exists or not ansible_check_mode
```

- [ ] **Step 7: `tasks/configure.yml`**

Create `roles/warp_exit/tasks/configure.yml`:

```yaml
---
- name: Check for the wgcf WireGuard profile
  ansible.builtin.stat:
    path: "{{ warp_exit_dir }}/wgcf-profile.conf"
  register: warp_exit_profile_file

- name: Require the wgcf profile outside check mode
  ansible.builtin.assert:
    that:
      - warp_exit_profile_file.stat.exists
    fail_msg: "{{ warp_exit_dir }}/wgcf-profile.conf is missing after registration; cannot build {{ warp_exit_interface }}.conf."
  when: not ansible_check_mode

- name: Build and activate the WARP interface
  when: warp_exit_profile_file.stat.exists
  block:
    - name: Read the wgcf WireGuard profile
      ansible.builtin.slurp:
        src: "{{ warp_exit_dir }}/wgcf-profile.conf"
      register: warp_exit_profile_raw
      no_log: true

    - name: Parse the wgcf WireGuard profile
      ansible.builtin.set_fact:
        warp_exit_profile: "{{ warp_exit_profile_raw.content | b64decode | parse_wgcf_profile }}"
      no_log: true

    # diff is off because the file holds the private key; a check run shows `ok` or `changed` instead.
    - name: Render WARP interface config
      ansible.builtin.template:
        src: warp.conf.j2
        dest: "{{ warp_exit_dir }}/{{ warp_exit_interface }}.conf"
        owner: root
        group: root
        mode: "0600"
      register: warp_exit_conf
      no_log: true
      diff: false

    - name: Enable the WARP interface at boot
      ansible.builtin.systemd:
        name: "wg-quick@{{ warp_exit_interface }}"
        enabled: true

    # A handler would restart only at the end of the play, after verify.yml has already
    # judged the old tunnel; restart inline so verification sees the new config.
    - name: Restart the WARP interface after a config change
      ansible.builtin.systemd:
        name: "wg-quick@{{ warp_exit_interface }}"
        state: restarted
      when: warp_exit_conf is changed

    - name: Ensure the WARP interface is up
      ansible.builtin.systemd:
        name: "wg-quick@{{ warp_exit_interface }}"
        state: started
```

- [ ] **Step 8: `tasks/verify.yml`**

Create `roles/warp_exit/tasks/verify.yml`:

```yaml
---
- name: Verify traffic leaves through WARP
  ansible.builtin.shell:
    cmd: |
      set -o pipefail
      trace="$(curl --interface {{ warp_exit_interface }} --silent --max-time 5 {{ warp_exit_trace_url }})" || trace=""
      warp_on=no
      if printf '%s\n' "$trace" | grep -qx 'warp=on'; then warp_on=yes; fi
      handshake="$(wg show {{ warp_exit_interface }} latest-handshakes | awk 'NR == 1 { print $2 }')"
      echo "handshake=${handshake:-none} warp_on=${warp_on}"
      [ -n "$handshake" ] && [ "$handshake" != "0" ] && [ "$warp_on" = yes ]
    executable: /bin/bash
  register: warp_exit_verify
  retries: "{{ warp_exit_verify_retries }}"
  delay: "{{ warp_exit_verify_delay }}"
  until: warp_exit_verify.rc == 0
  failed_when: false
  changed_when: false
  when: not ansible_check_mode

- name: Fail closed when WARP does not carry traffic
  ansible.builtin.fail:
    msg: >-
      WARP exit on {{ inventory_hostname }} does not carry traffic after {{ warp_exit_verify_retries }} attempts
      ({{ warp_exit_verify.stdout | default('no output') }}). The node is not deployed on this host:
      its profile routes client traffic into the {{ warp_exit_interface }} interface.
  when:
    - not ansible_check_mode
    - warp_exit_verify.rc != 0
```

- [ ] **Step 9: `tasks/reregister.yml`**

Create `roles/warp_exit/tasks/reregister.yml`:

```yaml
---
- name: Refuse WARP re-registration in check mode
  ansible.builtin.assert:
    that:
      - not ansible_check_mode
    fail_msg: "warp_reregister replaces the WARP account and cannot run in check mode."

- name: Re-register WARP, restoring the previous registration on failure
  block:
    - name: Set the WARP backup directory
      ansible.builtin.set_fact:
        warp_exit_backup_dir: "{{ warp_exit_dir }}/backup-{{ ansible_date_time.iso8601_basic_short }}"

    - name: Create the WARP backup directory
      ansible.builtin.file:
        path: "{{ warp_exit_backup_dir }}"
        state: directory
        owner: root
        group: root
        mode: "0700"

    - name: Back up the current WARP registration
      ansible.builtin.copy:
        src: "{{ warp_exit_dir }}/{{ item }}"
        dest: "{{ warp_exit_backup_dir }}/{{ item }}"
        remote_src: true
        owner: root
        group: root
        mode: "0600"
      loop:
        - wgcf-account.toml
        - wgcf-profile.conf
        - "{{ warp_exit_interface }}.conf"
      no_log: true

    - name: Remove the old WARP registration
      ansible.builtin.file:
        path: "{{ warp_exit_dir }}/{{ item }}"
        state: absent
      loop:
        - wgcf-account.toml
        - wgcf-profile.conf

    - name: Register the replacement WARP account
      ansible.builtin.import_tasks: register.yml

    - name: Rebuild the WARP interface on the new registration
      ansible.builtin.import_tasks: configure.yml

    - name: Verify the new WARP registration
      ansible.builtin.import_tasks: verify.yml

  rescue:
    - name: Restore the previous WARP registration
      ansible.builtin.copy:
        src: "{{ warp_exit_backup_dir }}/{{ item }}"
        dest: "{{ warp_exit_dir }}/{{ item }}"
        remote_src: true
        owner: root
        group: root
        mode: "0600"
      loop:
        - wgcf-account.toml
        - wgcf-profile.conf
        - "{{ warp_exit_interface }}.conf"
      no_log: true

    - name: Restart the WARP interface on the previous registration
      ansible.builtin.systemd:
        name: "wg-quick@{{ warp_exit_interface }}"
        state: restarted

    - name: Report the failed re-registration
      ansible.builtin.fail:
        msg: >-
          WARP re-registration failed on {{ inventory_hostname }}; the previous registration was restored
          from {{ warp_exit_backup_dir }} and the tunnel restarted on it.
```

- [ ] **Step 10: `tasks/probe.yml` и systemd-шаблоны**

Create `roles/warp_exit/tasks/probe.yml`:

```yaml
---
- name: Ensure WARP probe directories
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: root
    group: root
    mode: "0755"
  loop:
    - "{{ warp_exit_probe_dir }}"
    - "{{ warp_exit_textfile_dir }}"

- name: Install the WARP probe script
  ansible.builtin.copy:
    src: warp-probe.sh
    dest: "{{ warp_exit_probe_script }}"
    owner: root
    group: root
    mode: "0755"

- name: Install WARP probe systemd units
  ansible.builtin.template:
    src: "{{ item }}.j2"
    dest: "/etc/systemd/system/{{ item }}"
    owner: root
    group: root
    mode: "0644"
  loop:
    - warp-probe.service
    - warp-probe.timer
  register: warp_exit_probe_units

- name: Reload systemd after WARP probe unit changes
  ansible.builtin.systemd:
    daemon_reload: true
  when: warp_exit_probe_units is changed

- name: Enable the WARP probe timer
  ansible.builtin.systemd:
    name: warp-probe.timer
    enabled: true
    state: started
```

Create `roles/warp_exit/templates/warp-probe.service.j2`:

```ini
# {{ ansible_managed }}
[Unit]
Description=Probe traffic through the WARP exit for node-exporter
After=wg-quick@{{ warp_exit_interface }}.service

[Service]
Type=oneshot
Environment=WARP_PROBE_IFACE={{ warp_exit_interface }}
Environment=WARP_PROBE_OUT={{ warp_exit_textfile_dir }}/warp.prom
ExecStart={{ warp_exit_probe_script }}
```

Create `roles/warp_exit/templates/warp-probe.timer.j2`:

```ini
# {{ ansible_managed }}
[Unit]
Description=Run the WARP probe every minute

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5

[Install]
WantedBy=timers.target
```

- [ ] **Step 11: Подключить роль в `playbook.yml`**

В таске `Resolve effective feature flags` после строки `effective_feature_node_tuning: ...` добавить:

```yaml
        effective_warp_mode: "{{ fleet_host_cfg.get('remnawave', {}).get('warp_mode', 'none') | string | trim | lower }}"
```

В секции `roles:` между записями `monitoring_stack` и `remnawave_node` добавить:

```yaml
    # No role-level tags on purpose: they would be inherited by the `never`-tagged
    # re-registration tasks. Tags live in roles/warp_exit/tasks/main.yml.
    - role: warp_exit
      when: >-
        effective_run_main_stack | bool
        and (effective_feature_remnawave_node | bool)
        and effective_warp_mode != 'none'
```

- [ ] **Step 12: Снять запрет WARP в `node_tuning`**

В `roles/node_tuning/tasks/main.yml` заменить таску `Validate supported tuning values` на:

```yaml
- name: Validate supported tuning values
  ansible.builtin.assert:
    that:
      - remnawave_ipv6_state_effective in ["enabled", "disabled"]
    fail_msg: "remnawave_ipv6_state must be enabled|disabled."
  tags: [remnawave, bbr, ipv6]
```

Удалить строку `remnawave_warp_enabled: false` из `roles/node_tuning/defaults/main.yml`, `group_vars/all.yml` и `group_vars/remnawave_node.yml`.

Run: `grep -rn "remnawave_warp_enabled" . --include='*.yml' --include='*.j2' --include='*.py' --include='*.sh'`
Expected: пустой вывод.

- [ ] **Step 13: Textfile-коллектор в `monitoring_agent`**

В `roles/monitoring_agent/templates/docker-compose.yml.j2` заменить блок `command:` у `node_exporter` на:

```yaml
    command:
      - --path.rootfs=/host
      # warp_exit writes warp.prom here; on hosts without WARP the directory stays empty.
      - --collector.textfile.directory=/host{{ monitoring_agent_textfile_dir }}
```

В `roles/monitoring_agent/defaults/main.yml` после `monitoring_agent_cadvisor_image` добавить:

```yaml
monitoring_agent_textfile_dir: /var/lib/node_exporter/textfile
```

В `roles/monitoring_agent/tasks/main.yml` сразу после таски `Ensure monitoring agent install directory exists` добавить:

```yaml
- name: Ensure node-exporter textfile collector directory exists
  ansible.builtin.file:
    path: "{{ monitoring_agent_textfile_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0755"
  when: monitoring_agent_enable_node_exporter | bool
```

- [ ] **Step 14: Тест тегов проходит**

Run: `bash .github/scripts/test-warp-exit-tags.sh`
Expected: 6 строк `PASS: --tags '...' does not include re-registration`, `PASS: --tags warp includes the tunnel verification`, `PASS: --tags warp_reregister runs re-registration only`, `All warp_exit tag selection tests passed.`

Если `--list-tasks` показывает таски `import_tasks` внутри `block` у `reregister.yml` без тегов `never`/`warp_reregister` — перенести теги на сам `block` (`tags: [never, warp_reregister]`) и повторить тест. Тест обязан пройти до коммита.

- [ ] **Step 15: Синтаксис и линтеры**

Run:
```bash
ansible-playbook -i hosts.example.ini playbook.yml --syntax-check
ansible-lint playbook.yml roles/warp_exit roles/monitoring_agent roles/node_tuning
yamllint playbook.yml roles/warp_exit roles/monitoring_agent roles/node_tuning group_vars .github/workflows/ansible-ci.yml
```
Expected: `playbook: playbook.yml`, ansible-lint `Passed`, yamllint без ошибок.

- [ ] **Step 16: Добавить шаг в CI**

В `.github/workflows/ansible-ci.yml` сразу после шага `Syntax check playbook.yml`:

```yaml
      - name: warp_exit tag selection test
        run: bash .github/scripts/test-warp-exit-tags.sh
```

- [ ] **Step 17: Commit**

```bash
git add roles/warp_exit playbook.yml roles/monitoring_agent roles/node_tuning group_vars .github/scripts/test-warp-exit-tags.sh .github/workflows/ansible-ci.yml
git commit -m "feat(warp_exit): install, verify and re-register the WARP exit

Hosts with remnawave.warp_mode other than none register with wgcf when they have
no registration, build warp.conf from the generated profile and must pass a
traffic check through the tunnel before remnawave_node runs, so a reinstalled
node can no longer come up routing clients into a missing interface.
warp_reregister backs up the registration, replaces it and restores it if the
new one does not carry traffic. Tags sit on the role's tasks rather than its
playbook entry, since an inherited tag would let --tags warp trigger the
never-tagged re-registration; a test pins this. node_tuning no longer rejects
WARP, and node-exporter reads the probe's textfile metrics.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Smoke-проверка WARP

**Files:**
- Modify: `.github/scripts/smoke-remnawave.sh` (после блока `if [[ "$feature_remnawave_node" == "true" ]]; then ... fi`)

**Interfaces:**
- Consumes: `fleet_hosts.<alias>.remnawave.warp_mode` из Task 1.

- [ ] **Step 1: Реализация**

После блока проверки `remnanode` добавить:

```bash
  warp_mode="$(jq -r --arg alias "$alias" '.fleet_hosts[$alias].remnawave.warp_mode // "none"' "$runtime_vars")"
  if [[ "$feature_remnawave_node" == "true" && "$warp_mode" != "none" ]]; then
    # A node with a WARP profile but no working tunnel passes every other check while
    # sending client traffic into a dead interface.
    echo "[smoke][$alias] Check WARP exit carries traffic (warp_mode=$warp_mode)"
    run_ansible "$alias" -b -m ansible.builtin.shell -a "curl --interface warp --silent --max-time 10 https://www.cloudflare.com/cdn-cgi/trace | grep -qx warp=on" >/dev/null
  fi
```

- [ ] **Step 2: Проверка синтаксиса и логики выбора хостов**

Run:
```bash
bash -n .github/scripts/smoke-remnawave.sh
printf '{"fleet_hosts":{"a":{"remnawave":{"warp_mode":"all"}},"b":{"remnawave":{"warp_mode":"none"}},"c":{"remnawave":{}}}}' > /tmp/warp-smoke-vars.json
for h in a b c; do jq -r --arg alias "$h" '.fleet_hosts[$alias].remnawave.warp_mode // "none"' /tmp/warp-smoke-vars.json; done; rm -f /tmp/warp-smoke-vars.json
```
Expected: `bash -n` без вывода; затем `all`, `none`, `none`.

- [ ] **Step 3: Обновить раздел smoke в `docs/OPERATIONS_GUIDE.md`**

В разделе `## 8) Smoke-проверки после deploy` после строки `- sysctl BBR/IPv6 (если \`feature_node_tuning=true\`).` добавить:

```markdown
- `warp=on` в ответе `https://www.cloudflare.com/cdn-cgi/trace` через интерфейс `warp` (если `feature_remnawave_node=true` и `remnawave.warp_mode` не `none`).
```

- [ ] **Step 4: Commit**

```bash
git add .github/scripts/smoke-remnawave.sh docs/OPERATIONS_GUIDE.md
git commit -m "feat(smoke): check that WARP carries traffic on WARP nodes

Every existing smoke check passes on a node whose profile routes clients into a
dead warp interface. Add a warp=on check through the interface for hosts whose
warp_mode is not none.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Документация

**Files:**
- Modify: `README.md` (раздел `## Роли`), `docs/ROLE_CATALOG.md` (после раздела `### \`node_tuning\``, и строка про интерфейс в разделе `warp_mode`), `docs/OPERATIONS_GUIDE.md` (раздел `### Cloudflare WARP на ноде`)

- [ ] **Step 1: `README.md`**

В списке `## Роли` после строки `- \`node_tuning\` — BBR + IPv6.` добавить:

```markdown
- `warp_exit` — выход Cloudflare WARP на ноде (`wgcf` + `wg-quick@warp`): регистрация, проверка трафика до деплоя ноды, перерегистрация по тегу `warp_reregister`, проба для алертов. Включается `remnawave.warp_mode` не равным `none`.
```

- [ ] **Step 2: `docs/ROLE_CATALOG.md`**

После раздела `### \`node_tuning\`` (перед `### \`monitoring_agent\``) вставить:

````markdown
### `warp_exit`
- Назначение: выход Cloudflare WARP на ноде Remnawave — интерфейс `warp`, в который профиль панели отправляет outbound `WARP`.
- Включается: `feature_remnawave_node=true` и `remnawave.warp_mode` = `all` или `inbound`. При `none` роль не запускается.
- Что делает при обычном деплое:
  - ставит `wireguard-tools` и `wgcf` фиксированной версии со сверкой SHA256;
  - если нет `/etc/wireguard/wgcf-account.toml` — регистрирует хост (`wgcf register` + `generate`); существующую регистрацию не трогает;
  - собирает `/etc/wireguard/warp.conf` из `wgcf-profile.conf` (`Table = off`, MTU 1280, только IPv4, без DNS) и перезапускает туннель только при изменении файла;
  - **fail-closed**: до 5 попыток проверяет handshake и `warp=on` через интерфейс; не прошло — деплой хоста падает до `remnawave_node`;
  - ставит `warp-probe.timer`, который раз в минуту пишет метрики в `/var/lib/node_exporter/textfile/warp.prom`.
- Параметры (`roles/warp_exit/defaults/main.yml`):
  - `warp_exit_wgcf_version` / `warp_exit_wgcf_sha256` — версия и хэш `wgcf`. Неверный хэш → таска `Install pinned wgcf binary` падает.
  - `warp_exit_interface` (default `warp`) — менять нельзя без правки профилей панели: они ссылаются на это имя.
  - `warp_exit_verify_retries` (default `5`), `warp_exit_verify_delay` (default `6`).
- Теги: `warp` (обычный путь), `warp_reregister` (только перерегистрация). Теги стоят на тасках роли, а не на её записи в `playbook.yml` — иначе `--tags warp` запустил бы перерегистрацию. Это проверяет `.github/scripts/test-warp-exit-tags.sh`.
- Метрики и алерты: `warp_probe_success_ratio`, `warp_probe_latency_avg_seconds`, `warp_status_on`, `warp_handshake_age_seconds`, `warp_probe_last_run_timestamp_seconds`; алерты `WarpTunnelDown`, `WarpDegraded`, `WarpProbeStale` в группе `warp-exit` (`roles/monitoring_stack/templates/fleet-alerts.yml.j2`).

Пример:

```yaml
hosts:
  dh-germ-1:
    remnawave:
      warp_mode: all
```
````

В разделе `### Cloudflare WARP: \`warp_mode\`` заменить абзац

```markdown
Требование к хосту: WARP-интерфейс должен называться `warp` (`streamSettings.sockopt.interface: warp`),
иначе xray не поднимет outbound.
```

на

```markdown
Интерфейс `warp` на хосте ставит роль `warp_exit` — она включается тем же `warp_mode`.
Профиль ссылается на него через `streamSettings.sockopt.interface: warp`.
При неправильном значении `warp_mode` падает и рендер fleet (`render-fleet-runtime.py`), и sync.
```

- [ ] **Step 3: `docs/OPERATIONS_GUIDE.md`**

В разделе `### Cloudflare WARP на ноде` заменить последнюю строку `На хосте WARP-интерфейс обязан называться \`warp\`.` на:

````markdown
Интерфейс `warp` на хосте разворачивает роль `warp_exit`. Ручной установки не нужно,
в том числе после переустановки ОС: хост без регистрации зарегистрируется сам при deploy,
и нода не поднимется, пока трафик через WARP не пройдёт проверку.

**Перерегистрация WARP** — когда пришёл алерт `WarpDegraded` (бесплатная регистрация
со временем начинает терять запросы при свежем handshake):

```bash
gh workflow run deploy-remnawave-node.yml --ref master \
  -f target=remnawave -f mode=deploy -f limit=<alias> \
  -f tags=warp_reregister -f run_smoke=false -f panel_sync_write=false
```

Роль сохранит текущую регистрацию в `/etc/wireguard/backup-<время>/`, создаст новую
и проверит трафик. Если новая не заработала — вернёт старую и упадёт с сообщением об откате.
Выходной IP после перерегистрации может смениться в пределах диапазона Cloudflare.

**Что делать по алертам:**

| Алерт | Что значит | Действие |
|---|---|---|
| `WarpTunnelDown` | 3 минуты ни один запрос через WARP не прошёл | `systemctl status wg-quick@warp`, `wg show warp`; если туннель поднят, а трафика нет — перерегистрация |
| `WarpDegraded` | больше 20% запросов через WARP неуспешны за 15 минут | перерегистрация (команда выше) |
| `WarpProbeStale` | проба не отчитывалась больше 5 минут | `systemctl status warp-probe.timer warp-probe.service`, `journalctl -u warp-probe.service` |
````

- [ ] **Step 4: Проверка ссылок и отсутствия устаревших формулировок**

Run: `grep -rn "intentionally unsupported\|remnawave_warp_enabled" README.md docs/ roles/ group_vars/ playbook.yml`
Expected: пустой вывод.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/ROLE_CATALOG.md docs/OPERATIONS_GUIDE.md
git commit -m "docs: document the warp_exit role, re-registration and WARP alerts

Covers what the role does per warp_mode, its parameters and tags, the
re-registration command with its backup and rollback, and what an operator
should do for each of the three WARP alerts.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: PR и выкат

Каждый шаг с `gh workflow run` меняет прод. Шаги выполняются по одному; на любом «Expected», которое не сошлось, — стоп и доклад, без импровизации на хосте.

- [ ] **Step 1: Полный локальный прогон всех тестов**

Run:
```bash
python3 .github/scripts/test-render-fleet-runtime.py && \
python3 .github/scripts/test-warp-exit-filter.py && \
python3 .github/scripts/test-warp-exit-template.py && \
bash .github/scripts/test-warp-probe.sh && \
TMPDIR=/tmp PROMTOOL="docker run --rm -v /tmp:/tmp --entrypoint promtool prom/prometheus:v2.55.0" bash .github/scripts/test-warp-alerts.sh && \
bash .github/scripts/test-warp-exit-tags.sh && \
python3 .github/scripts/test-remnawave-api-sync.py && \
ansible-playbook -i hosts.example.ini playbook.yml --syntax-check && \
ansible-lint playbook.yml playbook.openwrt.yml playbook-yusic-worker.yml roles && \
yamllint .
```
Expected: всё проходит, код выхода 0.

- [ ] **Step 2: PR**

```bash
git push -u origin feat/warp-role
gh pr create --base master --title "feat(warp_exit): WARP exit role with fail-closed verification and degradation alerts" --body-file - <<'EOF'
WARP exits on dh-germ-1 and tw-germ-1 were installed by hand, and `node_tuning` rejected WARP outright. A reinstalled `warp_mode: all` node therefore came up green in the panel and in smoke checks while its default outbound pointed at a missing `warp` interface. dh-germ-1 is about to be reinstalled.

This adds `roles/warp_exit`, keyed on `remnawave.warp_mode`:

- registers the host with `wgcf` when it has no registration, and leaves an existing one alone;
- builds `warp.conf` from the generated profile, byte-identical to the live tw-germ-1 file, and restarts the tunnel only when the file changes;
- fails the host before `remnawave_node` unless a request through the interface returns `warp=on` with a live handshake;
- `tags=warp_reregister` backs up the registration, replaces it, and restores the old one if the new one does not carry traffic;
- a probe timer writes success ratio, latency, `warp=on` and handshake age to node-exporter's textfile collector, with `WarpTunnelDown`, `WarpDegraded` and `WarpProbeStale` alerts going to the existing Telegram topic.

Tags are set on the role's tasks, not on its `playbook.yml` entry. An inherited tag would let `--tags warp` run the `never`-tagged re-registration and change the exit IP; `test-warp-exit-tags.sh` pins this.

Also: `render-fleet-runtime.py` validates `warp_mode` with the panel sync's rules, smoke checks `warp=on` on WARP nodes, and the `node_tuning` WARP guard is removed.

Tests: filter and template contract tests (the template is compared byte for byte with the live file shape), a probe test with fake `curl`/`wg`, `promtool test rules` against Prometheus 2.55.0, and the tag-selection test.

Spec: `docs/superpowers/specs/2026-09-16-warp-exit-role-design.md`. Plan: `docs/superpowers/plans/2026-09-16-warp-exit-role.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

Expected: ссылка на PR. Дождаться зелёного `lint-and-syntax` и `molecule`.

- [ ] **Step 3: Ревью ботов**

Через ~5 минут после открытия:
```bash
gh api "repos/NerRobDog/Ansible_Servers/pulls/<N>/comments" --jq '.[] | "[\(.user.login)] \(.path):\(.line // .original_line)\n\(.body)\n---"'
```
Каждое замечание P0/P1 — исправить отдельным коммитом и ответить `Addressed in <SHA> — <одна строка>`. Мердж — только после этого и после согласия пользователя.

- [ ] **Step 4: Мердж (squash) после согласия пользователя**

```bash
gh pr merge <N> --squash --delete-branch
```

- [ ] **Step 5: tw-germ-1, check-mode**

```bash
gh workflow run deploy-remnawave-node.yml --ref master -f target=remnawave -f mode=deploy -f limit=tw-germ-1 -f tags=warp -f check_mode=true -f run_smoke=false -f panel_sync_write=false
```
Expected в логе прогона: `Render WARP interface config` — `ok` (НЕ `changed`); `Register a new WARP account` — `ok`/`skipped`; `Install pinned wgcf binary` — `changed` (ожидаемая замена бинарника, см. отступление 5). Любой `changed` у `Render WARP interface config` — стоп: шаблон не совпал с живым файлом.

- [ ] **Step 6: tw-germ-1, реальный прогон**

Перед запуском зафиксировать время старта туннеля:
```bash
ssh -i ~/.ssh/ansible_actions deploy@5.42.127.98 'systemctl show -p ActiveEnterTimestamp wg-quick@warp'
```

```bash
gh workflow run deploy-remnawave-node.yml --ref master -f target=remnawave -f mode=deploy -f limit=tw-germ-1 -f tags=warp,monitoring -f check_mode=false -f run_smoke=true -f panel_sync_write=false
```

После прогона:
```bash
ssh -i ~/.ssh/ansible_actions deploy@5.42.127.98 'systemctl show -p ActiveEnterTimestamp wg-quick@warp; curl -s --interface warp -m 10 https://www.cloudflare.com/cdn-cgi/trace | grep -E "^(ip|warp)="; sudo -n stat -c "%a %n" /etc/wireguard/wgcf-account.toml; systemctl is-active warp-probe.timer; sleep 70; cat /var/lib/node_exporter/textfile/warp.prom | grep -v "^#"'
```
Expected: `ActiveEnterTimestamp` у `wg-quick@warp` совпадает с зафиксированным до прогона (туннель не перезапускался); `ip=104.28.197.9`, `warp=on`; `600 /etc/wireguard/wgcf-account.toml`; таймер `active`; в `warp.prom` `warp_probe_success_ratio` близко к 1 и `warp_status_on 1`. Воркфлоу и smoke — зелёные.

- [ ] **Step 7: ae-us-1, правила**

```bash
gh workflow run deploy-remnawave-node.yml --ref master -f target=remnawave -f mode=deploy -f limit=ae-us-1 -f tags=monitoring -f check_mode=false -f run_smoke=true -f panel_sync_write=false
```

После прогона:
```bash
ssh -i ~/.ssh/ansible_actions deploy@193.233.130.242 'curl -s 127.0.0.1:9090/api/v1/rules | python3 -c "import sys,json; print([r[\"name\"] for g in json.load(sys.stdin)[\"data\"][\"groups\"] if g[\"name\"]==\"warp-exit\" for r in g[\"rules\"]])"; curl -s "127.0.0.1:9090/api/v1/query?query=warp_probe_success_ratio" | head -c 300; echo; curl -s 127.0.0.1:9090/api/v1/alerts | grep -o "Warp[A-Za-z]*" | sort -u'
```
Expected: `['WarpTunnelDown', 'WarpDegraded', 'WarpProbeStale']`; ряд `warp_probe_success_ratio` для tw-germ-1 есть; активных `Warp*`-алертов нет.

- [ ] **Step 8: Остальные ноды, textfile-коллектор**

```bash
gh workflow run deploy-remnawave-node.yml --ref master -f target=remnawave -f mode=deploy -f limit=tw-nyc-1,tw-spb-1,dh-neth-2 -f tags=monitoring -f check_mode=false -f run_smoke=true -f panel_sync_write=false
```
Expected: зелёный прогон и smoke; `FleetNodeExporterDown` для этих хостов не появился.

- [ ] **Step 9: dh-germ-1 после переустановки и bootstrap**

Предусловие: пользователь переустановил ОС, bootstrap (`mode=bootstrap tags=user,tailscale`) прошёл, tailnet-IP в fleet актуален.

```bash
gh workflow run deploy-remnawave-node.yml --ref master -f target=remnawave -f mode=deploy -f limit=dh-germ-1 -f check_mode=false -f run_smoke=true -f panel_sync_write=true
```
Expected в логе: `Register a new WARP account` — `changed`; `Verify traffic leaves through WARP` — ok; `remnawave_node` выполнен после неё; smoke с `Check WARP exit carries traffic` — зелёный. Через 15 минут на ae-us-1 нет активных `Warp*`-алертов для dh-germ-1.

- [ ] **Step 10: Закрыть задачи**

```bash
bd close as-prr --reason "warp_exit role merged and rolled out to tw-germ-1, ae-us-1 rules, fleet textfile collector and reinstalled dh-germ-1"
```
