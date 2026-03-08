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

## 3) Что нужно хранить в GitHub

Используйте GitHub Environment (например `production`).

### Обязательные Secrets

- `RW_FLEET_CONFIG_B64` — base64 от JSON/YAML-конфига серверов.
- `ANSIBLE_SSH_PRIVATE_KEY` — приватный ключ, которым потом идёт деплой.

### Опциональные Secrets

- `RW_PANEL_API_TOKEN` — если используете API в роли remnawave.
- `ANSIBLE_VAULT_PASSWORD` — если используете vault-зашифрованные данные.

## 4) Формат fleet-конфига (до base64)

Ниже пример YAML (можно JSON):

```yaml
defaults:
  deploy_user: deploy
  ansible_port: 22
  features:
    feature_base: true
    feature_docker: true
    feature_remnawave_node: false
    feature_caddy_node: false
    feature_node_tuning: false
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
    remnawave:
      node_secret_key: "SECRET_FROM_PANEL"
      node_port: 3001
      caddy_domain: "node1.example.com"
      caddy_monitor_port: 8443
      ipv6_state: enabled
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
    custom_roles:
      - test_stack
```

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
   - `mode`: `bootstrap`, `deploy` или `lockdown`;
   - `limit`: `all` или `host1,host2`;
   - `check_mode`: сначала `true`, потом `false`.

## 7) Рекомендуемая последовательность для нового хоста

1. Добавить хост в fleet-конфиг и обновить `RW_FLEET_CONFIG_B64`.
2. Запустить `mode=bootstrap` c `limit=<новый-host>`.
3. Запустить `mode=lockdown` c `limit=<новый-host>`.
4. Затем обычный `mode=deploy` для всех.

## 8) Частые ошибки

- `Host alias not found`: в `limit` указан alias, которого нет в fleet-конфиге.
- `Bootstrap password is missing`: для bootstrap режима не задан пароль.
- `Custom role not found`: роль указана в `custom_roles`, но каталога `roles/<name>` нет.
- `Missing remnawave_node_secret_key`: включена node-роль, но не передан секрет ноды.

## 9) Что делать помощнику при изменениях

1. Прочитать:
   - `docs/ROLE_CATALOG.md`
   - `docs/DOCUMENTATION_RULES.md`
2. Внести правки.
3. Прогнать:
   - `ansible-playbook -i hosts.example.ini playbook.yml --syntax-check`
   - `ansible-lint playbook.yml roles`
   - `yamllint .`
4. Обновить документацию и примеры, если менялся контракт.
