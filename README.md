# Ansible_Servers

Универсальный Ansible-проект для раскатки одинаковой базы и опциональных ролей на 1..N серверов.

Главная модель:
- серверы описываются только в GitHub Environment Secret `RW_FLEET_CONFIG_B64`;
- профили RemaWave хранятся в git-шаблонах `remnawave/profiles/*.json` и синкаются в панель pre-step'ом;
- workflow запускается вручную в режимах `bootstrap`, `deploy`, `lockdown`;
- push в репозиторий для добавления новых серверов не нужен.

## Основные документы

- Подробная инструкция для операторов: [`docs/OPERATIONS_GUIDE.md`](docs/OPERATIONS_GUIDE.md)
- Настройка секретов (RU, пошагово): [`docs/SECRETS_SETUP_RU.md`](docs/SECRETS_SETUP_RU.md)
- Описание ролей и feature flags: [`docs/ROLE_CATALOG.md`](docs/ROLE_CATALOG.md)
- Правила документирования для помощников: [`docs/DOCUMENTATION_RULES.md`](docs/DOCUMENTATION_RULES.md)
- Onboarding нового помощника: [`docs/ASSISTANT_ONBOARDING.md`](docs/ASSISTANT_ONBOARDING.md)

## Роли

- `base` — базовые пакеты.
- `firewall` — UFW политика `deny incoming` + allow для SSH/443.
- `docker` — установка Docker CE.
- `remnawave_node` — deploy RemaWave node.
- `caddy_node` — TLS decoy для self-steal Reality + локальный health endpoint.
- `node_tuning` — BBR + IPv6.
- `user_shell` — пользователь/sudo/SSH shell.
- `ssh_lockdown` — отключение password auth и root SSH login.
- `custom_roles` — дополнительные локальные роли из `roles/`, задаются по хостам.

## Workflow

Файл: `.github/workflows/deploy-remnawave-node.yml`

Inputs:
- `environment` — GitHub Environment c секретами флота.
- `mode` — `bootstrap | deploy | lockdown`.
- `limit` — `all` или alias-хостов через запятую.
- `check_mode` — dry-run.
- `run_smoke` — post-deploy smoke-проверки (`true|false`).
- `tags` — опциональный фильтр ansible tags.
- `panel_sync_write` — `true|false` для write/read-only API sync профилей и назначений нод.

Pre-step перед Ansible:
- `.github/scripts/remnawave-api-sync.py`
- манифест: `remnawave/profile-sync.yml`
- шаблоны: `remnawave/profiles/*.json`

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

Опциональные:
- `RW_PROFILE_VARS_B64` — опциональный global placeholder map. Основные Reality-поля задаются per-host в fleet; `reality_short_id/private_key` можно не задавать (будут сгенерированы из `node_secret_key`).
- `ANSIBLE_VAULT_PASSWORD`

Environment Variables:
- `RW_PANEL_API_BASE_URL` — базовый URL панели (например, `https://panel.example.com`).
- `REMNAWAVE_API_ENDPOINT_TEMPLATE` — старый optional source для host runtime vars (можно оставить пустым).

## Локальные проверки

```bash
ansible-galaxy collection install -r requirements.yml
python3 -m pip install -r requirements.txt
mkdir -p .ansible/tmp
python .github/scripts/test-render-fleet-runtime.py
ANSIBLE_LOCAL_TEMP=.ansible/tmp ANSIBLE_REMOTE_TEMP=.ansible/tmp ansible-playbook -i hosts.example.ini playbook.yml --syntax-check
ansible-lint playbook.yml roles
yamllint .
```

## Локальный smoke-run после deploy

```bash
.github/scripts/smoke-remnawave.sh \
  --inventory .ansible/runtime/hosts.ini \
  --runtime-vars .ansible/runtime/runtime_vars.json \
  --limit de_node,nl_node
```

## Быстрый operational flow

1. Обновить `RW_FLEET_CONFIG_B64` в нужном Environment.
2. Запустить `mode=bootstrap` для новых хостов.
3. Запустить `mode=lockdown` для этих же хостов.
4. Запускать регулярный `mode=deploy` с `run_smoke=true`.
