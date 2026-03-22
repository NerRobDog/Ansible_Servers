# Role Catalog

Ниже описаны роли и когда они должны запускаться.

## Core roles

### `base`
- Назначение: базовые пакеты и подготовка сервера.
- Дефолт: включена (`feature_base=true`).
- Когда выключать: только на хостах с очень специфичным образом ОС.

### `firewall`
- Назначение: UFW-политика по умолчанию (`deny incoming`, `allow outgoing`) и разрешение только нужных портов.
- Дефолт: включена (`feature_firewall=true`).
- Базовые allow-правила:
  - SSH (`firewall_ssh_port`, обычно 22/tcp)
  - 443/tcp (Reality/public endpoint)
- По умолчанию не открывает наружу порт Caddy monitor (`8443`).

### `docker`
- Назначение: установка Docker CE и плагинов.
- Дефолт: включена (`feature_docker=true`).
- Когда выключать: если Docker уже управляется внешней системой.

### `tailscale`
- Назначение: установка Tailscale, запуск сервиса и минимальный join в tailnet.
- Дефолт: выключена (`feature_tailscale=false`).
- Ключевые параметры:
  - `tailscale_auth_key_env` (по умолчанию `TAILSCALE_AUTH_KEY`)
  - `tailscale_extra_args` (доп. args для `tailscale up`)
- Поведение:
  - если хост уже авторизован, `tailscale up` не выполняется;
  - если хост не авторизован, нужен ключ из GitHub Secrets.

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

## Yusic worker roles

### `yusic_worker_relay`
- Назначение: подготовка relay-ноды для lifecycle внешних `download-worker` (пакеты `skopeo`, `sshpass`, staging dirs).
- Используется в `playbook-yusic-worker.yml`.

### `yusic_worker_deploy`
- Назначение: deploy/update воркеров через relay:
  - mirror immutable image (`sha-*`) в docker-archive,
  - transfer на worker,
  - `docker load` + `docker compose up -d`.
- Дополнительно: сохраняет backup image ref для rollback.

### `yusic_worker_smoke`
- Назначение: smoke-проверки на worker:
  - контейнер запущен,
  - `python services/download-worker/selfcheck.py` внутри контейнера проходит.

### `yusic_worker_rollback`
- Назначение: rollback worker на backup image, если deploy/update + smoke завершились ошибкой.

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
  feature_firewall: true
  feature_docker: true
  feature_tailscale: false
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

## OpenWrt roles

### `openwrt_rollback_guard`
- Назначение: авто-rollback guard перед изменениями OpenWrt (snapshot managed paths + пакеты, watchdog, confirm).
- Дефолт: включён для `deploy/lockdown`, отключён в `--check` и в `bootstrap`.
- Ключевые параметры:
  - `openwrt_rollback_enabled`
  - `openwrt_rollback_timeout_minutes`
  - `openwrt_rollback_reboot_on_restore`
  - `openwrt_rollback_snapshot_dir`
  - `openwrt_rollback_managed_paths`
  - `openwrt_rollback_managed_services`
- Поведение:
  - если confirm не выполнен после деплоя, запускается rollback и reboot.
  - подтверждение делается через `/usr/local/sbin/openwrt-rollback-guard confirm`.

### `openwrt_base`
- Назначение: проверка OpenWrt, базовые пакеты, bootstrap SSH key (в `bootstrap` режиме).
- Дефолт: включена (`feature_openwrt_base=true`).
- Ключевые параметры:
  - `openwrt_base_packages`
  - `openwrt_bootstrap_public_key`

### `openwrt_network_core`
- Назначение: managed baseline для `network` (loopback/LAN/WAN skeleton).
- Дефолт: выключена (`feature_openwrt_network_core=false`).
- Ключевые параметры:
  - `openwrt_network_lan_device`
  - `openwrt_network_lan_ipaddr`
  - `openwrt_network_lan_netmask`
  - `openwrt_network_lan_ip6assign`
  - `openwrt_network_ula_prefix`

### `openwrt_firewall_core`
- Назначение: managed baseline для `firewall` + fail-safe SSH.
- Дефолт: выключена (`feature_openwrt_firewall_core=false`).
- Поведение:
  - всегда держит allow SSH из LAN;
  - при включённом ZeroTier добавляет allow SSH из `openwrt_firewall_zerotier_src_cidr`.
- Ключевые параметры:
  - `openwrt_firewall_allow_zerotier_ssh`
  - `openwrt_firewall_zerotier_src_cidr`

### `openwrt_wan`
- Назначение: managed конфиг `network.wan` для разных провайдеров.
- Дефолт: выключена (`feature_openwrt_wan=false`), включайте осознанно во fleet.
- В `openwrt_profile=prod_update` требует явный opt-in: `feature_openwrt_wan_apply_in_prod=true`.
- Поддерживаемые режимы:
  - `openwrt_wan_proto=dhcp`
  - `openwrt_wan_proto=static` (нужны `openwrt_wan_ipaddr` + `openwrt_wan_netmask`)
  - `openwrt_wan_proto=pppoe` (нужны `openwrt_wan_pppoe_username` + `openwrt_wan_pppoe_password`)
- Ключевые параметры:
  - `openwrt_wan_device` (обычно `eth0` или DSA-имя интерфейса)
  - `openwrt_wan_gateway`
  - `openwrt_wan_dns` (list)
  - `openwrt_wan_pppoe_ipv6` (`auto|0|1`)
  - `openwrt_wan_boot_try_dhcp_first` (`true|false`, default `true`)
  - `openwrt_wan_boot_try_dhcp_wait_sec`
  - `openwrt_wan_boot_try_dhcp_probe_host`
  - `openwrt_wan_boot_try_dhcp_probe_count`
  - `openwrt_wan_boot_try_dhcp_service_name`
  - `openwrt_wan_boot_try_dhcp_start_priority`
- Поведение:
  - роль меняет только `network.wan`;
  - при изменении делает `uci commit network` + `network reload/restart`.
  - для `static|pppoe` может ставить boot-service `wan_failover`: на старте сначала пробует DHCP, при неуспехе применяет configured fallback профиль WAN.

### `openwrt_zerotier`
- Назначение: установка ZeroTier, UCI-конфиг и join к сети.
- Дефолт: включена (`feature_openwrt_zerotier=true`).
- Ключевые параметры:
  - `zerotier_network_id` (обязателен при включённой роли)
  - `zerotier_manage_secret` / `zerotier_secret`
- Примечание: может работать одновременно с `feature_tailscale=true`.

### `openwrt_passwall2`
- Назначение: полностью managed `/etc/config/passwall2` из шаблона.
- Дефолт: включена (`feature_openwrt_passwall2=true`).
- Политика безопасности: `passwall2.global.enabled=0` по умолчанию (safe mode).
- Ключевые параметры:
  - `passwall2_subscribe_url` (обязателен)
  - `passwall2_probe_url`
  - `passwall2_socks_port`
  - `passwall2_profile_overrides`
  - `passwall2_acl_bypass_macs`
  - `passwall2_auto_enable_when_nodes`
  - `passwall2_require_nodes_for_enable`
- Шаблон разделён на логические блоки (rules/nodes/global/acl/runtime/subscribe) и собирается в единый managed конфиг.

### `openwrt_homeproxy_cleanup`
- Назначение: stop/disable/remove HomeProxy и его конфиг, чтобы не держать mixed-mode.
- Дефолт: включена (`feature_openwrt_homeproxy_cleanup=true`).

### `openwrt_docker_runtime`
- Назначение: managed Docker runtime на OpenWrt (пакеты/daemon/service) без destructive reset.
- Дефолт: включена (`feature_openwrt_docker_runtime=true`).
- Ключевые параметры:
  - `openwrt_docker_manage_runtime`
  - `openwrt_docker_runtime_packages`
  - `openwrt_docker_runtime_manage_daemon_config`
  - `openwrt_docker_runtime_daemon_config`

### `openwrt_docker_stacks`
- Назначение: управляемые docker-compose стеки на OpenWrt.
- Дефолт: включена (`feature_openwrt_docker_stacks=true`).
- Ключевые параметры:
  - `openwrt_docker_compose_command`
  - `openwrt_docker_stacks`

### `openwrt_monitoring_agent`
- Назначение: OpenWrt exporter + textfile probes (direct и via Passwall2).
- Дефолт: включена (`feature_openwrt_monitoring_agent=true`).
- Ключевые параметры:
  - `openwrt_node_exporter_port`
  - `openwrt_probe_interval_minutes`
  - `passwall2_probe_url`
  - `passwall2_socks_port`

### `openwrt_ssh_lockdown`
- Назначение: выключение `PasswordAuth` и `RootPasswordAuth` в Dropbear.
- Дефолт: выключена (`feature_openwrt_ssh_lockdown=false`).
- Обычно включается в режиме workflow `lockdown`.

## OpenWrt feature flags reference

```yaml
features:
  feature_openwrt_base: true
  feature_openwrt_network_core: false
  feature_openwrt_firewall_core: false
  feature_openwrt_wan: false
  feature_openwrt_wan_apply_in_prod: false
  feature_openwrt_zerotier: true
  feature_tailscale: false
  feature_openwrt_passwall2: true
  feature_openwrt_homeproxy_cleanup: true
  feature_openwrt_docker_runtime: true
  feature_openwrt_docker_stacks: true
  feature_openwrt_monitoring_agent: true
  feature_openwrt_ssh_lockdown: false
```

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
