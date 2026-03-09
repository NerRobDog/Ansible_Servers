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

Перед `ansible-playbook` workflow выполняет panel pre-step:
- upsert Config Profiles из `remnawave/profiles/*.json`;
- назначение profile/inbounds существующим нодам в панели.

## 3) Что нужно хранить в GitHub

Используйте GitHub Environment (например `production`).

### Обязательные Secrets

- `RW_FLEET_CONFIG_B64` — base64 от JSON/YAML-конфига серверов.
- `ANSIBLE_SSH_PRIVATE_KEY` — приватный ключ, которым потом идёт деплой.
- `RW_PANEL_API_TOKEN` — API токен панели RemaWave (Bearer).

### Опциональные Secrets

- `RW_PROFILE_VARS_B64` — optional global placeholder values (обычно не нужен).
- `ANSIBLE_VAULT_PASSWORD` — если используете vault-зашифрованные данные.

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
   - `mode`: `bootstrap`, `deploy` или `lockdown`;
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

## 7) Рекомендуемая последовательность для нового хоста

1. Добавить хост в fleet-конфиг и обновить `RW_FLEET_CONFIG_B64`.
2. Запустить `mode=bootstrap` c `limit=<новый-host>`.
3. Запустить `mode=lockdown` c `limit=<новый-host>`.
4. Затем обычный `mode=deploy` для всех с `run_smoke=true`.

## 8) Smoke-проверки после deploy

Автоматически (`run_smoke=true`) выполняются:
- SSH-доступ по ключу (`ansible ping`);
- `systemctl is-active docker` (если `feature_docker=true`);
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
