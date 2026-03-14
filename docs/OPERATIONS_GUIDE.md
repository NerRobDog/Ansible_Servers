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
- `clean`: one-shot `bootstrap -> lockdown -> idempotence gate` (с принудительным firewall).

Перед `ansible-playbook` workflow выполняет panel pre-step:
- upsert Config Profiles из `remnawave/profiles/*.json`;
- назначение profile/inbounds существующим нодам в панели.
Для мониторинга используется отдельный workflow:
- `.github/workflows/monitor-remnawave-node.yml`
- запускается вручную (`workflow_dispatch`) или по расписанию (`cron`);
- выполняет smoke-checks и может отправлять статус smoke в Telegram.

Прод-алерты по метрикам/состоянию отправляет `Alertmanager` из роли `monitoring_stack`.

## 3) Что нужно хранить в GitHub

Используйте GitHub Environment (например `production`).

### Обязательные Secrets

- `RW_FLEET_CONFIG_B64` — base64 от JSON/YAML-конфига серверов.
- `ANSIBLE_SSH_PRIVATE_KEY` — приватный ключ, которым потом идёт деплой.
- `RW_PANEL_API_TOKEN` — API токен панели RemaWave (Bearer).
- `MONITORING_ALERT_TELEGRAM_BOT_TOKEN` — отдельный bot token для Alertmanager.
- `MONITORING_ALERT_TELEGRAM_CHAT_ID` — chat id для Alertmanager.

### Опциональные Secrets

- `RW_PROFILE_VARS_B64` — optional global placeholder values (обычно не нужен).
- `ANSIBLE_VAULT_PASSWORD` — если используете vault-зашифрованные данные.
- `MONITORING_ALERT_TELEGRAM_TOPIC_ID` — topic id (message_thread_id), если Alertmanager должен писать в конкретный топик.
- `ALERT_TELEGRAM_BOT_TOKEN` / `ALERT_TELEGRAM_CHAT_ID` / `ALERT_TELEGRAM_TOPIC_ID` — только для workflow `monitor-remnawave-node`.

### Обязательная Environment Variable

- `RW_PANEL_API_BASE_URL` — базовый URL панели, например `https://panel.example.com`.

## 4) Формат fleet-конфига (до base64)

Ниже пример YAML (можно JSON):

```yaml
defaults:
  deploy_user: deploy
  ansible_port: 22
  features:
    feature_base: true
    feature_firewall: false
    feature_docker: true
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
      # Optional: allow panel health-check API access to NODE_PORT via UFW.
      panel_allowed_sources:
        - "89.23.98.20/32"
      target_inbound_tags: []
    firewall:
      ssh_allowed_sources:
        - "0.0.0.0/0"
        - "::/0"
      extra_allowed_tcp_ports: []
      extra_allowed_udp_ports: []
    monitoring:
      # Для single-host схемы (node + monitoring на одном VPS) безопасно:
      # agent_bind_address: "172.17.0.1"
      # Для multi-host скрейпа с отдельным monitoring-server оставьте доступный адрес (например 0.0.0.0 + firewall).
      agent_bind_address: "172.17.0.1"
      agent_node_exporter_port: 9100
      agent_cadvisor_port: 8080
      agent_promtail_enabled: true
      # Если пусто, URL вычисляется автоматически на основе stack-host.
      loki_push_url: ""
      labels:
        country: "DE"
        role: "node"
      # Закрывает exporter-порты для всех, кроме указанных источников (DOCKER-USER chain).
      # Для single-host можно оставить false.
      agent_acl_enabled: false
      agent_acl_allowed_sources:
        - "198.51.100.10/32" # IP monitoring-сервера (или DE-ноды-скрейпера)
      # Порты Grafana/Prometheus/Loki/Alertmanager: только loopback (доступ через SSH forward).
      stack_bind_address: "127.0.0.1"
      # Порт Loki для ingest логов от нод. Обычно оставляем 0.0.0.0 + allow-list.
      stack_loki_ingest_bind_address: "0.0.0.0"
      stack_loki_ingest_allowed_sources:
        - "203.0.113.10/32"
        - "203.0.113.11/32"
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

Правило мониторинга:
- если где-то включён `feature_monitoring_agent` или `feature_monitoring_stack`, должен быть ровно один host с `feature_monitoring_stack=true`.

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
   - `mode`: `bootstrap`, `deploy`, `lockdown` или `clean`;
   - `limit`: `all` или `host1,host2`;
   - `check_mode`: сначала `true`, потом `false` (`mode=clean` требует `false`);
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
   - `notify_on_success` (`false`, если нужны только фейлы smoke).

### SSH forward для приватного доступа к мониторингу

Если `monitoring.stack_bind_address: "127.0.0.1"`, открывайте UI так:

```bash
ssh -i ~/.ssh/ansible_actions -L 13000:127.0.0.1:3000 deploy@<host>
```

Тогда Grafana будет доступна локально: `http://127.0.0.1:13000`.

## 7) Рекомендуемая последовательность для нового хоста

1. Добавить хост в fleet-конфиг и обновить `RW_FLEET_CONFIG_B64`.
2. Запустить `mode=clean` c `limit=<новый-host>`.
3. Затем обычный `mode=deploy` для всех с `run_smoke=true`.

## 8) Smoke-проверки после deploy

Автоматически (`run_smoke=true`) выполняются:
- SSH-доступ по ключу (`ansible ping`);
- `systemctl is-active docker` (если `feature_docker=true`);
- контейнер `remnanode` в host network + `NET_ADMIN` (если `feature_remnawave_node=true`);
- `caddy validate` + `https://<domain>:<monitor_port>/healthz` (если `feature_caddy_node=true`);
- sysctl BBR/IPv6 (если `feature_node_tuning=true`).
- для `feature_monitoring_agent=true`: контейнеры `node_exporter`, `cadvisor`, `promtail` и доступность `/metrics`.
- для `feature_monitoring_stack=true`: готовность Prometheus/Alertmanager/Grafana/Loki и наличие provisioning/dashboards файлов.
- coverage monitoring-targets проверяется только для хостов из `--limit` (limit-scoped).

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
- `check_mode=true is not supported for mode=clean`: clean выполняет реальный provisioning и строгий idempotence gate.
- `Custom role not found`: роль указана в `custom_roles`, но каталога `roles/<name>` нет.
- `Missing remnawave_node_secret_key`: включена node-роль, но не передан секрет ноды.
- `Profile ... not found`/`missing inbound tags`: mismatch манифеста sync и профилей в панели.
- Нет интернета у клиентов при активной подписке: часто `dest` в панели указывает на `:443` этой же ноды вместо локального decoy.
- `Exactly one host with feature_monitoring_stack=true...`: включён monitoring_agent без stack-host или stack-host больше одного.
- `Invalid monitoring_stack settings ... MONITORING_ALERT_TELEGRAM_*`: не заданы alert secrets для stack-host.
- `Prometheus target down` после включения ACL: проверьте `monitoring.agent_acl_allowed_sources` (должен содержать IP скрейпера с `/32`).
- `Loki ingest blocked`: проверьте `monitoring.stack_loki_ingest_allowed_sources` на stack-host.

## 11) Data Map: что мониторим и где смотреть

| Сигнал | Источник | Где смотреть | Тип алерта | Что делать |
|---|---|---|---|---|
| CPU/RAM/Disk/Uptime хоста | `node_exporter` | Grafana `Remna Fleet Overview`, `Remna Node Drilldown` | warning/critical | Проверить нагрузку процесса, свободный диск, лимиты VPS |
| Состояние контейнеров remnanode/caddy | `cadvisor` | Grafana (panel restarts), Prometheus targets | warning | Проверить `docker ps`, `docker logs`, перезапуски |
| Логи remnanode/caddy | `promtail` (agent) -> `loki` | Grafana Explore / logs panels | info/warning (через правила по метрикам) | Сопоставить с временем деградации, проверить inbound/outbound ошибки |
| Доступность exporters/stack | Prometheus scrape `up` | Grafana + Alertmanager | critical | Проверить сеть/ACL/firewall, статус контейнеров monitoring |
| Конфигурационные ошибки мониторинга | Prometheus self-metrics | Alertmanager + Grafana | warning | Проверить `prometheus.yml`, `alerts.yml`, релоад конфигов |

## 10) Что делать помощнику при изменениях

1. Прочитать:
   - `docs/ROLE_CATALOG.md`
   - `docs/DOCUMENTATION_RULES.md`
2. Внести правки.
3. Прогнать:
   - `python .github/scripts/test-render-fleet-runtime.py`
   - `ansible-playbook -i hosts.example.ini playbook.yml --syntax-check`
   - `ansible-lint playbook.yml roles`
   - `yamllint .`
4. Обновить документацию и примеры, если менялся контракт.

## 12) Настройка Telegram topic для алертов

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
   - `MONITORING_ALERT_TELEGRAM_BOT_TOKEN`
   - `MONITORING_ALERT_TELEGRAM_CHAT_ID`
   - `MONITORING_ALERT_TELEGRAM_TOPIC_ID`

Проверка:
- вызовите тестовый alert (например временно остановите `monitoring-node-exporter`);
- убедитесь, что сообщение Alertmanager пришло в нужный топик.
