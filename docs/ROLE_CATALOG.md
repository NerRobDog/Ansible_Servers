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
  feature_user_shell: false
```

Правило:
- база включена почти везде;
- дополнительные роли включайте только там, где это действительно нужно.
