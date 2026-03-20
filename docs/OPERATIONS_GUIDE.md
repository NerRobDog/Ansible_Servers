# Operations Guide

Подробная инструкция для людей без глубокого опыта в Ansible/GitHub Actions.

## 1) Что делает этот репозиторий

Репозиторий настраивает серверы через Ansible ролями.

Базовый принцип:
- вы меняете **только секрет конфигурации флота** в GitHub;
- запускаете workflow вручную;
- Ansible применяет одинаковую базу и опциональные роли по флагам на каждом хосте.

## 2) Как устроен деплой

Workflow: `.github/workflows/deploy-remnawave-node.yml`

Режимы:
- `bootstrap`: первый вход по паролю, копирование SSH-ключа, создание deploy-user.
- `deploy`: обычный деплой только по SSH-ключу.
- `lockdown`: отключение SSH входа по паролю и root login.

Target:
- `target=remnawave` — текущий flow панели + Ansible playbook `playbook.yml`.
- `target=yusic_worker` — lifecycle внешних воркеров через relay (`playbook-yusic-worker.yml`), без panel sync.

Перед `ansible-playbook` workflow выполняет panel pre-step:
- upsert Config Profiles из `remnawave/profiles/*.json`;
- назначение profile/inbounds существующим нодам в панели.
Для мониторинга используется отдельный workflow:
- `.github/workflows/monitor-remnawave-node.yml`
- запускается вручную (`workflow_dispatch`) или по расписанию (`cron`);
- выполняет smoke-checks и отправляет алерты в Telegram topic.

## 3) Что нужно хранить в GitHub

Используйте GitHub Environment (например `production`).

### Обязательные Secrets

- `RW_FLEET_CONFIG_B64` — base64 от JSON/YAML-конфига серверов.
- `OPENWRT_FLEET_CONFIG_B64` — base64 от JSON/YAML-конфига OpenWrt роутеров (для OpenWrt workflow).
- `ANSIBLE_SSH_PRIVATE_KEY` — приватный ключ, которым потом идёт деплой.
- `RW_PANEL_API_TOKEN` — API токен панели RemaWave (Bearer).
- `TAILSCALE_AUTH_KEY` — auth key для автоприсоединения хостов с `feature_tailscale=true`, если они ещё не joined.
- `ZEROTIER_API_TOKEN` — токен ZeroTier Central API для read+authorize в OpenWrt workflow.

### Опциональные Secrets

- `RW_PROFILE_VARS_B64` — optional global placeholder values (обычно не нужен).
- `ANSIBLE_VAULT_PASSWORD` — если используете vault-зашифрованные данные.
- `ALERT_TELEGRAM_BOT_TOKEN` — bot token для уведомлений.
- `ALERT_TELEGRAM_CHAT_ID` — chat id чата/группы Telegram.
- `ALERT_TELEGRAM_TOPIC_ID` — topic id (message_thread_id), если отправка нужна в конкретный топик.
- `ALERT_TELEGRAM_TOPIC_ID_OPENWRT` — отдельный topic id для OpenWrt алертов (fallback на общий).
- `ZEROTIER_NETWORK_ID` — default network id для OpenWrt ZT API sync (можно не задавать, если network_id указан в fleet).

### Обязательная Environment Variable

- `RW_PANEL_API_BASE_URL` — базовый URL панели, например `https://panel.example.com`.

Для `target=yusic_worker` `RW_PANEL_API_TOKEN`/`RW_PANEL_API_BASE_URL` не используются в runtime path, но могут оставаться в Environment для совместимости.

## 4) Формат fleet-конфига (до base64)

Ниже пример YAML (можно JSON):

```yaml
defaults:
  deploy_user: deploy
  ansible_port: 22
  features:
    feature_base: true
    feature_firewall: true
    feature_docker: true
    feature_tailscale: false
    feature_remnawave_node: false
    feature_caddy_node: false
    feature_node_tuning: false
    feature_monitoring_agent: false
    feature_monitoring_stack: false
    feature_user_shell: false

hosts:
  node-1:
    ansible_host: 203.0.113.10
    bootstrap:
      username: root
      password: "first-login-password"
    deploy_user: deploy
    features:
      feature_remnawave_node: true
      feature_caddy_node: true
      feature_node_tuning: true
      feature_monitoring_agent: true
    remnawave:
      node_secret_key: "SECRET_FROM_PANEL"
      node_port: 3001
      caddy_domain: "node1.example.com"
      caddy_monitor_port: 8443
      ipv6_state: enabled
      caddy_tls_mode: public
      caddy_local_only: true
      caddy_tls_cert_file: ""
      caddy_tls_key_file: ""
      caddy_acme_ca: ""
      panel_node_uuid: "00000000-0000-4000-8000-000000000001"
      # Optional. Empty => profile name == host alias.
      target_profile_name: ""
      # Optional. Empty => VLESS_<HOST_ALIAS>.
      inbound_tag: ""
      reality_target: "127.0.0.1:8443"
      # Optional: if empty, generated deterministically from node_secret_key.
      reality_short_id: ""
      # Optional: if empty, generated deterministically from node_secret_key.
      reality_private_key: ""
      reality_server_name: "node1.example.com"
      target_inbound_tags: []
    monitoring:
      agent_bind_address: "0.0.0.0"
      agent_node_exporter_port: 9100
      agent_cadvisor_port: 8080
      stack_retention_days: 7
      stack_grafana_admin_user: "admin"
      stack_grafana_admin_password: "CHANGE_ME_STRONG_PASSWORD"
    custom_roles:
      - test_stack

  test-vm:
    ansible_host: 203.0.113.20
    bootstrap:
      username: root
      password: "another-password"
    features:
      feature_remnawave_node: false
      feature_caddy_node: false
      feature_node_tuning: false
      feature_monitoring_agent: false
      feature_monitoring_stack: false
    custom_roles:
      - test_stack
```

Дополнительно для внешних yusic-воркеров:

```yaml
defaults:
  yusic_worker:
    relay_host_alias: tw-germ-1
    image_repo: ghcr.io/OWNER/Yusic_bot/download-worker
    enabled: true
    arch: arm64
    tags: ["provider:soundcloud", "region:nl", "cpu:low"]
    max_concurrent_jobs: 1
    network_mode: host
    dns: ["1.1.1.1", "8.8.8.8"]
    redis_url: redis://100.64.0.10:6379/0
    cache_bot_token: "<CACHE_BOT_TOKEN>"
    inline_cache_chat_id: "-1001234567890"

workers:
  wrt_me:
    ssh:
      host: 100.112.10.20
      port: 22
      user: root
    tags: ["provider:soundcloud", "region:nl", "cpu:low"]
```

### Важно для Reality self-steal

Для рабочего трафика:
- `dest` на хосте в панели RemnaWave должен быть `127.0.0.1:<caddy_monitor_port>`;
- `sni/serverNames` должны совпадать с `caddy_domain`;
- `flow` клиента и сервера должен совпадать (`xtls-rprx-vision`).

Нельзя использовать `dest=<ваш_домен>:443`, если Reality inbound уже слушает `:443` — это вызывает петлю соединений.

### Важно для API sync нод

- Рекомендуется заполнять `remnawave.panel_node_uuid` явно.
- `remnawave.target_profile_name` и `remnawave.inbound_tag` можно не задавать: по умолчанию это `<host_alias>` и `VLESS_<HOST_ALIAS>`.
- `remnawave.reality_short_id` и `remnawave.reality_private_key` опциональны: если пусто, API sync сгенерирует их детерминированно на основе `node_secret_key`.
- Если `panel_node_uuid` пустой, sync сначала ищет ноду по имени `hosts.<alias>`, затем по `ansible_host == node.address`.

## 5) Как обновить `RW_FLEET_CONFIG_B64`

1. Подготовьте файл `fleet.yaml`.
2. Закодируйте:
   ```bash
   base64 -i fleet.yaml | tr -d '\n'
   ```
3. Вставьте строку в `RW_FLEET_CONFIG_B64` в нужном Environment.

Пример через `gh`:

```bash
base64 -i fleet.yaml | tr -d '\n' | gh secret set RW_FLEET_CONFIG_B64 --env production
```

## 6) Как запускать workflow

В GitHub:
1. Actions -> `deploy-remnawave-node` -> Run workflow.
2. Выберите:
   - `environment` (например `production`);
   - `target`: `remnawave` или `yusic_worker`;
   - `mode`: `bootstrap`, `deploy` или `lockdown` (для `target=remnawave`);
   - `worker_mode`: `bootstrap|deploy|update|smoke` (для `target=yusic_worker`);
   - `worker_image_tag`: обязательный `sha-*` для `worker_mode=deploy|update`;
   - `limit`: `all` или `host1,host2`;
   - `check_mode`: сначала `true`, потом `false`;
   - `panel_sync_write`: `false` для read-only отчёта, `true` для применения изменений в панели.
   - `run_smoke`: `true` для автоматических пост-деплой проверок.

## 6.1) Шаг profile sync

Файлы:
- `remnawave/profile-sync.yml`
- `remnawave/profiles/*.json`

Формат placeholders в JSON-шаблонах: `${VAR_NAME}`.

Опциональный JSON для `RW_PROFILE_VARS_B64` (если хотите глобальные placeholders):
```json
{
  "RW_REALITY_TARGET": "127.0.0.1:8443",
  "RW_REALITY_SERVER_NAME": "daring.watchd0g.dev"
}
```

Мониторинг:
1. Actions -> `monitor-remnawave-node` -> Run workflow.
2. Выберите:
   - `environment`;
   - `limit`;
   - `notify_on_success` (`false`, если нужны только алерты при проблемах).

## 7) Рекомендуемая последовательность для нового хоста

1. Добавить хост в fleet-конфиг и обновить `RW_FLEET_CONFIG_B64`.
2. Запустить `mode=bootstrap` c `limit=<новый-host>`.
3. Запустить `mode=lockdown` c `limit=<новый-host>`.
4. Затем обычный `mode=deploy` для всех с `run_smoke=true`.

## 8) Smoke-проверки после deploy

Автоматически (`run_smoke=true`) выполняются:
- SSH-доступ по ключу (`ansible ping`);
- `systemctl is-active docker` (если `feature_docker=true`);
- `tailscale status --json` и рабочий backend state (если `feature_tailscale=true`);
- контейнер `remnanode` в host network + `NET_ADMIN` (если `feature_remnawave_node=true`);
- `caddy validate` + `https://<domain>:<monitor_port>/healthz` (если `feature_caddy_node=true`);
- sysctl BBR/IPv6 (если `feature_node_tuning=true`).

Ручной запуск того же набора:

```bash
.github/scripts/smoke-remnawave.sh \
  --inventory .ansible/runtime/hosts.ini \
  --runtime-vars .ansible/runtime/runtime_vars.json \
  --limit de_node,nl_node
```

## 9) Частые ошибки

- `Host alias not found`: в `limit` указан alias, которого нет в fleet-конфиге.
- `Bootstrap password is missing`: для bootstrap режима не задан пароль.
- `Custom role not found`: роль указана в `custom_roles`, но каталога `roles/<name>` нет.
- `Missing remnawave_node_secret_key`: включена node-роль, но не передан секрет ноды.
- `Profile ... not found`/`missing inbound tags`: mismatch манифеста sync и профилей в панели.
- Нет интернета у клиентов при активной подписке: часто `dest` в панели указывает на `:443` этой же ноды вместо локального decoy.
- `Telegram secrets are not configured`: не заданы `ALERT_TELEGRAM_BOT_TOKEN`/`ALERT_TELEGRAM_CHAT_ID` в выбранном Environment.

## 10) Что делать помощнику при изменениях

1. Прочитать:
   - `docs/ROLE_CATALOG.md`
   - `docs/DOCUMENTATION_RULES.md`
2. Внести правки.
3. Прогнать:
   - `python .github/scripts/test-render-fleet-runtime.py`
   - `python .github/scripts/test-render-openwrt-fleet-runtime.py`
   - `ansible-playbook -i hosts.example.ini playbook.yml --syntax-check`
   - `ansible-playbook -i hosts.example.ini playbook.openwrt.yml --syntax-check`
   - `ansible-lint playbook.yml playbook.openwrt.yml roles`
   - `yamllint .`
4. Обновить документацию и примеры, если менялся контракт.

## 11) Настройка Telegram topic для алертов

1. Создайте бота через `@BotFather`, получите token.
2. Добавьте бота в группу, где включены topics, и дайте право писать сообщения.
3. Получите `chat_id`:
   - отправьте любое сообщение в группу;
   - выполните `https://api.telegram.org/bot<TOKEN>/getUpdates`;
   - возьмите `message.chat.id` (для групп обычно начинается с `-100`).
4. Получите `topic id`:
   - отправьте сообщение в нужный топик;
   - снова вызовите `getUpdates`;
   - возьмите `message.message_thread_id`.
5. Сохраните в GitHub Environment secrets:
   - `ALERT_TELEGRAM_BOT_TOKEN`
   - `ALERT_TELEGRAM_CHAT_ID`
   - `ALERT_TELEGRAM_TOPIC_ID`

## 12) OpenWrt lifecycle (bootstrap -> deploy -> lockdown)

Workflow для раскатки роутеров:
- `.github/workflows/deploy-openwrt.yml`

Workflow для мониторинговых smoke-проверок роутеров:
- `.github/workflows/monitor-openwrt-fleet.yml`

Пример OpenWrt fleet-конфига:
- `fleet.openwrt.example.yml`

Рекомендуемая последовательность:
1. Обновите secret `OPENWRT_FLEET_CONFIG_B64`.
2. Запустите `deploy-openwrt` с `mode=bootstrap` и `limit` на новые роутеры.
3. Запустите `deploy-openwrt` с `mode=deploy` (сначала `check_mode=true`, затем `false`).
4. После проверки key-based доступа запустите `mode=lockdown`.
5. Включите расписание/ручные запуски `monitor-openwrt-fleet`.

WAN-профили во fleet (DHCP/static/PPPoE):
- включите `features.feature_openwrt_wan=true` (global или per-host);
- задайте `wan.proto` и параметры провайдера per-host.

Минимальные примеры:
```yaml
defaults:
  features:
    feature_openwrt_wan: true
  wan:
    proto: dhcp
    device: eth0

hosts:
  wrt_dhcp:
    wan:
      proto: dhcp
      device: eth0

  wrt_static:
    wan:
      proto: static
      device: eth0
      ipaddr: 192.0.2.10
      netmask: 255.255.255.0
      gateway: 192.0.2.1
      dns: [1.1.1.1, 8.8.8.8]

  wrt_pppoe:
    wan:
      proto: pppoe
      device: eth0
      pppoe_username: "YOUR_PPPoE_LOGIN"
      pppoe_password: "YOUR_PPPoE_PASSWORD"
      pppoe_ipv6: auto
```

Важно:
- `wan` хранится внутри `OPENWRT_FLEET_CONFIG_B64`, поэтому логин/пароль PPPoE не попадают в git;
- для `static` обязательны `ipaddr` и `netmask`;
- роль меняет только `network.wan` и перезагружает сеть только при изменении.

Rollback guard (v1.2):
- в `deploy/lockdown` при `check_mode=false` роль `openwrt_rollback_guard` автоматически вооружает watchdog;
- workflow подтверждает guard после успешного smoke;
- если деплой/проверки упали до confirm, роутер автоматически делает rollback и reboot;
- лог отката: `/root/.ansible-rollback/<run_id>/rollback.log`.

Локальный деплой с тем же контрактом:
- используйте `scripts/deploy-openwrt-local.sh` (он делает `ZT API sync (optional) -> access preflight -> deploy -> smoke -> confirm`);
- прямой запуск `ansible-playbook playbook.openwrt.yml` используйте только для диагностики, без гарантии полного rollback-контракта.

Что проверяет OpenWrt smoke:
- SSH-доступ;
- состояние rollback guard (`ARMED|CONFIRMED`);
- ZeroTier service (если фича включена);
- tailscale backend state (если `feature_tailscale=true`);
- managed Passwall2 (safe mode по умолчанию) + SOCKS probe только если proxy включён;
- dockerd (если включено);
- exporter + textfile probe-метрики;
- Dropbear lockdown-параметры (если включено).

Проверка:
- запустите `monitor-remnawave-node` вручную c `notify_on_success=true`;
- убедитесь, что сообщение пришло именно в нужный топик.
