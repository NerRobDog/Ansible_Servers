# Ansible_Servers

Универсальный Ansible-проект для раскатки одинаковой базы и опциональных ролей на 1..N серверов.

Главная модель:
- серверы описываются только в GitHub Environment Secret `RW_FLEET_CONFIG_B64`;
- workflow запускается вручную в режимах `bootstrap`, `deploy`, `lockdown`;
- push в репозиторий для добавления новых серверов не нужен.

## Основные документы

- Подробная инструкция для операторов: [`docs/OPERATIONS_GUIDE.md`](docs/OPERATIONS_GUIDE.md)
- Описание ролей и feature flags: [`docs/ROLE_CATALOG.md`](docs/ROLE_CATALOG.md)
- Правила документирования для помощников: [`docs/DOCUMENTATION_RULES.md`](docs/DOCUMENTATION_RULES.md)
- Onboarding нового помощника: [`docs/ASSISTANT_ONBOARDING.md`](docs/ASSISTANT_ONBOARDING.md)

## Роли

- `base` — базовые пакеты.
- `docker` — установка Docker CE.
- `remnawave_node` — deploy RemaWave node.
- `caddy_node` — Caddy для node-monitor endpoint.
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
- `tags` — опциональный фильтр ansible tags.

## Обязательные Secrets (per environment)

- `RW_FLEET_CONFIG_B64` — base64 от JSON/YAML fleet config.
- `ANSIBLE_SSH_PRIVATE_KEY` — приватный SSH-ключ для key-based доступа.

Опциональные:
- `RW_PANEL_API_TOKEN`
- `ANSIBLE_VAULT_PASSWORD`

## Локальные проверки

```bash
ansible-galaxy collection install -r requirements.yml
python3 -m pip install -r requirements.txt
mkdir -p .ansible/tmp
ANSIBLE_LOCAL_TEMP=.ansible/tmp ANSIBLE_REMOTE_TEMP=.ansible/tmp ansible-playbook -i hosts.example.ini playbook.yml --syntax-check
ansible-lint playbook.yml roles
yamllint .
```

## Быстрый operational flow

1. Обновить `RW_FLEET_CONFIG_B64` в нужном Environment.
2. Запустить `mode=bootstrap` для новых хостов.
3. Запустить `mode=lockdown` для этих же хостов.
4. Запускать регулярный `mode=deploy`.
