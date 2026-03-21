# Ansible_Servers

Универсальный Ansible-проект для раскатки одинаковой базы и опциональных ролей на 1..N серверов.

Главная модель:
- серверы описываются только в GitHub Environment Secret `RW_FLEET_CONFIG_B64`;
- роутеры OpenWrt описываются в GitHub Environment Secret `OPENWRT_FLEET_CONFIG_B64`;
- профили RemaWave хранятся в git-шаблонах `remnawave/profiles/*.json` и синкаются в панель pre-step'ом;
- workflow запускается вручную в режимах `bootstrap`, `deploy`, `lockdown`;
- health-мониторинг можно запускать вручную или по расписанию через отдельный workflow;
- push в репозиторий для добавления новых серверов не нужен.

## Основные документы

- Подробная инструкция для операторов: [`docs/OPERATIONS_GUIDE.md`](docs/OPERATIONS_GUIDE.md)
- Runbook по lifecycle yusic worker-нод: [`docs/YUSIC_WORKERS_RUNBOOK.md`](docs/YUSIC_WORKERS_RUNBOOK.md)
- Настройка секретов (RU, пошагово): [`docs/SECRETS_SETUP_RU.md`](docs/SECRETS_SETUP_RU.md)
- Описание ролей и feature flags: [`docs/ROLE_CATALOG.md`](docs/ROLE_CATALOG.md)
- Правила документирования для помощников: [`docs/DOCUMENTATION_RULES.md`](docs/DOCUMENTATION_RULES.md)
- Onboarding нового помощника: [`docs/ASSISTANT_ONBOARDING.md`](docs/ASSISTANT_ONBOARDING.md)
- Пример OpenWrt fleet-конфига: [`fleet.openwrt.example.yml`](fleet.openwrt.example.yml)

## Роли

- `base` — базовые пакеты.
- `firewall` — UFW политика `deny incoming` + allow для SSH/443.
- `docker` — установка Docker CE.
- `tailscale` — установка/запуск Tailscale и минимальный join в tailnet.
- `remnawave_node` — deploy RemaWave node.
- `caddy_node` — TLS decoy для self-steal Reality + локальный health endpoint.
- `node_tuning` — BBR + IPv6.
- `monitoring_agent` — node_exporter + cadvisor на нодах.
- `monitoring_stack` — Prometheus + Alertmanager + Grafana + Loki + Promtail.
- `user_shell` — пользователь/sudo/SSH shell.
- `ssh_lockdown` — отключение password auth и root SSH login.
- `yusic_worker_relay` — подготовка relay-ноды для внешних yusic worker.
- `yusic_worker_deploy` — deploy/update воркеров через relay (`skopeo` + transfer + compose up).
- `yusic_worker_smoke` — проверка контейнера и `download-worker` selfcheck.
- `yusic_worker_rollback` — rollback воркера на backup image при failed smoke.
- `custom_roles` — дополнительные локальные роли из `roles/`, задаются по хостам.
- `openwrt_base` — базовая подготовка OpenWrt и bootstrap key.
- `openwrt_network_core` — managed baseline секций `network` (LAN/loopback/WAN skeleton).
- `openwrt_firewall_core` — managed baseline секций `firewall` + fail-safe SSH (LAN + ZeroTier CIDR).
- `openwrt_wan` — managed настройка `network.wan` (DHCP/static/PPPoE).
- `openwrt_rollback_guard` — авто-rollback guard с snapshot/watchdog/confirm.
- `openwrt_zerotier` — join/config ZeroTier.
- `openwrt_passwall2` — полностью managed `/etc/config/passwall2`.
- `openwrt_homeproxy_cleanup` — удаление HomeProxy (миграция к Passwall2).
- `openwrt_docker_runtime` — managed Docker runtime (пакеты/daemon/service, без destructive reset).
- `openwrt_docker_stacks` — управляемые docker-compose стеки на OpenWrt.
- `openwrt_monitoring_agent` — OpenWrt exporter + textfile probes.
- `openwrt_ssh_lockdown` — отключение SSH password auth на OpenWrt.

## Workflow

### Deploy workflow

Файл: `.github/workflows/deploy-remnawave-node.yml`

Inputs:
- `environment` — GitHub Environment c секретами флота.
- `target` — `remnawave | yusic_worker`.
- `mode` — `bootstrap | deploy | lockdown`.
- `worker_mode` — `bootstrap | deploy | update | smoke` (используется только для `target=yusic_worker`).
- `worker_image_tag` — immutable tag `sha-*` (обязателен для `worker_mode=deploy|update`).
- `limit` — `all` или alias-хостов через запятую.
- `check_mode` — dry-run.
- `run_smoke` — post-deploy smoke-проверки (`true|false`).
- `tags` — опциональный фильтр ansible tags.
- `panel_sync_write` — `true|false` для write/read-only API sync профилей и назначений нод.

Pre-step перед Ansible:
- `.github/scripts/remnawave-api-sync.py`
- манифест: `remnawave/profile-sync.yml`
- шаблоны: `remnawave/profiles/*.json`

### Monitoring workflow

Файл: `.github/workflows/monitor-remnawave-node.yml`

Назначение:
- периодический smoke-monitoring доступности и базового health;
- отправка алертов в Telegram topic.

Inputs (manual run):
- `environment` — GitHub Environment c секретами флота и Telegram.
- `limit` — `all` или alias-хостов через запятую.
- `notify_on_success` — отправлять ли сообщения об успешных проверках.

### OpenWrt deploy workflow

Файл: `.github/workflows/deploy-openwrt.yml`

Inputs:
- `environment` — GitHub Environment c OpenWrt fleet secrets.
- `mode` — `bootstrap | deploy | lockdown`.
- `openwrt_profile` — `prod_update | fresh` (default: `prod_update`).
- `limit` — `all` или alias-хостов через запятую.
- `check_mode` — dry-run.
- `run_smoke` — post-deploy smoke-проверки.
- `tags` — опциональный фильтр ansible tags.

Rollback-контракт OpenWrt:
- в `deploy/lockdown` (без `check_mode`) guard автоматически вооружается;
- после успешного smoke workflow подтверждает guard;
- если job падает до confirm, watchdog выполняет rollback и reboot роутера.

OpenWrt profile-контракт:
- `fresh` — первичная раскатка после чистой установки;
- `prod_update` — безопасный режим обновлений;
- WAN изменения в `prod_update` блокируются, если не включен host feature `feature_openwrt_wan_apply_in_prod=true`.

### OpenWrt monitoring workflow

Файл: `.github/workflows/monitor-openwrt-fleet.yml`

Назначение:
- периодические smoke-проверки роутеров;
- уведомления в Telegram (отдельный topic через `ALERT_TELEGRAM_TOPIC_ID_OPENWRT`, либо fallback на общий topic).

## Reality Self-Steal (важно)

Для рабочей схемы Reality на ноде:
- `dest` в inbound должен быть локальным decoy: `127.0.0.1:<remnawave.caddy_monitor_port>`;
- `sni/serverNames` должны совпадать с `remnawave.caddy_domain`;
- `flow` клиента и сервера должны совпадать (`xtls-rprx-vision`);
- `:443` занят `rw-core`, поэтому Caddy decoy должен быть на отдельном порту (по умолчанию `8443`).

Параметры Caddy в fleet-конфиге:
- `remnawave.caddy_tls_mode`: `public|internal|files` (по умолчанию `public`);
- `remnawave.caddy_local_only`: `true|false` (по умолчанию `true`);
- `remnawave.caddy_tls_cert_file`/`remnawave.caddy_tls_key_file`: только для `files`.

## Обязательные Secrets (per environment)

- `RW_FLEET_CONFIG_B64` — base64 от JSON/YAML fleet config.
- `ANSIBLE_SSH_PRIVATE_KEY` — приватный SSH-ключ для key-based доступа.
- `RW_PANEL_API_TOKEN` — API токен панели RemaWave (нужен для pre-step sync).
- `OPENWRT_FLEET_CONFIG_B64` — base64 от JSON/YAML OpenWrt fleet config (для OpenWrt workflows).
- `TAILSCALE_AUTH_KEY` — auth key для автоматического `tailscale up` на хостах с `feature_tailscale=true`, если хост ещё не авторизован в tailnet.
- `ZEROTIER_API_TOKEN` — токен ZeroTier Central API для read+authorize в OpenWrt workflow.

Опциональные:
- `RW_PROFILE_VARS_B64` — опциональный global placeholder map. Основные Reality-поля задаются per-host в fleet; `reality_short_id/private_key` можно не задавать (будут сгенерированы из `node_secret_key`).
- `ANSIBLE_VAULT_PASSWORD`
- `ALERT_TELEGRAM_BOT_TOKEN` — bot token для отправки оповещений.
- `ALERT_TELEGRAM_CHAT_ID` — chat id группы/канала (для групп обычно начинается с `-100`).
- `ALERT_TELEGRAM_TOPIC_ID` — topic id (message thread id) для форум-топика.
- `ALERT_TELEGRAM_TOPIC_ID_OPENWRT` — topic id для OpenWrt алертов (fallback на `ALERT_TELEGRAM_TOPIC_ID`).
- `ZEROTIER_NETWORK_ID` — опциональный default network id для OpenWrt ZT API sync (если не указан в host vars).

Feature flags:
- `feature_tailscale`: общий флаг для server и OpenWrt контуров (по умолчанию `false`).
- `feature_openwrt_zerotier` и `feature_tailscale` могут быть включены одновременно.
- `feature_openwrt_network_core`, `feature_openwrt_firewall_core`: базовые managed network/firewall роли.
- `feature_openwrt_docker_runtime`, `feature_openwrt_docker_stacks`: runtime/stacks управление Docker на OpenWrt.

Environment Variables:
- `RW_PANEL_API_BASE_URL` — базовый URL панели (например, `https://panel.example.com`).
- `REMNAWAVE_API_ENDPOINT_TEMPLATE` — старый optional source для host runtime vars (можно оставить пустым).

Для `target=yusic_worker` panel sync не запускается, но `RW_FLEET_CONFIG_B64` должен содержать секции:
- `defaults.yusic_worker`
- `workers.<alias>`

## Локальные проверки

```bash
ansible-galaxy collection install -r requirements.yml
python3 -m pip install -r requirements.txt
mkdir -p .ansible/tmp
python .github/scripts/test-render-fleet-runtime.py
python .github/scripts/test-render-openwrt-fleet-runtime.py
ANSIBLE_LOCAL_TEMP=.ansible/tmp ANSIBLE_REMOTE_TEMP=.ansible/tmp ansible-playbook -i hosts.example.ini playbook.yml --syntax-check
ANSIBLE_LOCAL_TEMP=.ansible/tmp ANSIBLE_REMOTE_TEMP=.ansible/tmp ansible-playbook -i hosts.example.ini playbook.openwrt.yml --syntax-check
ansible-lint playbook.yml playbook.openwrt.yml roles
yamllint .
```

## Локальный smoke-run после deploy

```bash
.github/scripts/smoke-remnawave.sh \
  --inventory .ansible/runtime/hosts.ini \
  --runtime-vars .ansible/runtime/runtime_vars.json \
  --limit de_node,nl_node
```

```bash
.github/scripts/smoke-openwrt.sh \
  --inventory .ansible/runtime/openwrt_hosts.ini \
  --runtime-vars .ansible/runtime/openwrt_runtime_vars.json \
  --limit wrt_de,wrt_nl
```

## Локальный OpenWrt deploy (официальный путь)

```bash
scripts/deploy-openwrt-local.sh \
  --inventory .ansible/runtime/openwrt_hosts.ini \
  --runtime-vars .ansible/runtime/openwrt_runtime_vars.json \
  --bootstrap-map .ansible/runtime/openwrt_bootstrap_map.json \
  --mode deploy \
  --profile prod_update \
  --limit wrt_de,wrt_nl
```

Скрипт выполняет `deploy -> smoke -> confirm rollback guard`.
Прямой `ansible-playbook playbook.openwrt.yml` допустим для диагностики, но не даёт полного rollback-контракта.

## Быстрый operational flow

1. Обновить `RW_FLEET_CONFIG_B64` в нужном Environment.
2. Запустить `mode=bootstrap` для новых хостов.
3. Запустить `mode=lockdown` для этих же хостов.
4. Запускать регулярный `mode=deploy` с `run_smoke=true`.
5. Включить/запускать `monitor-remnawave-node` для регулярных алертов в Telegram topic.
