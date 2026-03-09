# Role Catalog

Ниже описаны роли и когда они должны запускаться.

## Core roles

### `base`
- Назначение: базовые пакеты и подготовка сервера.
- Дефолт: включена (`feature_base=true`).
- Когда выключать: только на хостах с очень специфичным образом ОС.

### `docker`
- Назначение: установка Docker CE и плагинов.
- Дефолт: включена (`feature_docker=true`).
- Когда выключать: если Docker уже управляется внешней системой.

### `remnawave_node`
- Назначение: deploy контейнера `remnawave/node`.
- Требует:
  - `feature_remnawave_node=true`
  - `remnawave.node_secret_key`
- Основные параметры:
  - `remnawave.node_port`
  - `remnawave.node_secret_key`

### `caddy_node`
- Назначение: TLS decoy для Reality self-steal + health endpoint.
- Требует:
  - `feature_caddy_node=true`
  - `remnawave.caddy_domain`
- Основные параметры:
  - `remnawave.caddy_monitor_port`
  - `remnawave.caddy_domain`
  - `remnawave.caddy_tls_mode` (`public|internal|files`)
  - `remnawave.caddy_local_only` (`true|false`)
  - `remnawave.caddy_tls_cert_file`/`remnawave.caddy_tls_key_file` (для `files`)

Правило для Reality:
- `dest` должен указывать на локальный decoy, например `127.0.0.1:8443`.
- Не используйте `dest=<node-domain>:443`, если inbound Reality слушает `:443` (это вызывает петлю).
- `sni/serverNames` должны совпадать с `remnawave.caddy_domain`.
- `flow` на клиенте и сервере должен совпадать (`xtls-rprx-vision`).

### `node_tuning`
- Назначение: BBR + IPv6 sysctl-политика.
- Требует: `feature_node_tuning=true`.
- Основной параметр:
  - `remnawave.ipv6_state` = `enabled|disabled`.

### `monitoring_agent`
- Назначение: запуск `node_exporter` и `cadvisor` на ноде для удалённого scrape.
- Требует: `feature_monitoring_agent=true`.
- Основные параметры:
  - `monitoring.agent_bind_address` (по умолчанию `0.0.0.0`)
  - `monitoring.agent_node_exporter_port` (по умолчанию `9100`)
  - `monitoring.agent_cadvisor_port` (по умолчанию `8080`)

### `monitoring_stack`
- Назначение: центральный стек `Prometheus + Alertmanager + Grafana + Loki + Promtail`.
- Требует: `feature_monitoring_stack=true`.
- Основные параметры:
  - `monitoring.stack_retention_days`
  - `monitoring.stack_grafana_admin_user`
  - `monitoring.stack_grafana_admin_password`
- Подключение нод:
  - автоматически берёт хосты с `feature_monitoring_agent=true` из `fleet_hosts`.
  - скрапит `node_exporter`/`cadvisor` по `ansible_host` и monitoring-портам.

### `user_shell`
- Назначение: пользователь, authorized_keys, sudo, shell-окружение.
- Обычно:
  - в `bootstrap` включается автоматически для создания `deploy_user`;
  - в обычном `deploy` включается только если `feature_user_shell=true`.

### `ssh_lockdown`
- Назначение: отключение SSH password auth и root login.
- Запуск: в режиме `lockdown`.

## Custom roles

### `custom_roles`
- Источник: список `custom_roles` в fleet-конфиге конкретного хоста.
- Выполнение: после всех core-ролей.
- Ограничение: роль должна существовать в `roles/<name>`.

Пример:

```yaml
hosts:
  test-vm:
    ansible_host: 203.0.113.20
    custom_roles:
      - test_stack
```

## Feature flags reference

```yaml
features:
  feature_base: true
  feature_docker: true
  feature_remnawave_node: false
  feature_caddy_node: false
  feature_node_tuning: false
  feature_monitoring_agent: false
  feature_monitoring_stack: false
  feature_user_shell: false
```

Правило:
- база включена почти везде;
- дополнительные роли включайте только там, где это действительно нужно.

## RemaWave Panel Sync (CI pre-step)

Workflow перед Ansible выполняет sync панели через `.github/scripts/remnawave-api-sync.py`.

Host-level поля в fleet config:

```yaml
hosts:
  de_node:
    ansible_host: 5.42.127.98
    remnawave:
      panel_node_uuid: "00000000-0000-4000-8000-000000000001"
      target_profile_name: ""
      inbound_tag: ""
      reality_target: "127.0.0.1:8443"
      reality_short_id: ""
      reality_private_key: ""
      target_inbound_tags:
        - VLESS_DE_NODE
```

Правила:
- `panel_node_uuid` рекомендуется всегда, чтобы не полагаться на match по адресу.
- `target_profile_name` по умолчанию = alias хоста; можно задать вручную.
- `inbound_tag` по умолчанию = `VLESS_<HOST_ALIAS>`; можно задать вручную.
- `reality_short_id` и `reality_private_key` опциональны: при пустых значениях генерируются детерминированно из `node_secret_key`.
- `target_inbound_tags` по умолчанию берётся из сгенерированного `inbound_tag`.
