# Настройка секретов GitHub Actions (Fleet + RemaWave API Sync)

Этот документ объясняет, как настроить деплой так, чтобы менять только Secrets/Variables в GitHub Environment, без коммитов `hosts.ini`.

## 1) Что вы редактируете

В обычной работе меняются только:
1. `RW_FLEET_CONFIG_B64` (секрет с описанием серверов).
2. Host-level Reality поля внутри fleet (`inbound_tag`, при необходимости `reality_short_id`/`reality_private_key`).
3. При необходимости `RW_PANEL_API_TOKEN` и `RW_PANEL_API_BASE_URL`.

Шаблоны:
- `fleet.two-servers.example.yml` — пример для 2 серверов.
- `fleet.example.yml` — общий multi-server шаблон.
- `remnawave/profile-sync.yml` — правила sync профилей/нод в панели.
- `remnawave/profiles/*.json` — JSON-шаблоны config profile без секретов.

## 2) Где взять значения для RemaWave

### 2.1 `node_secret_key` (для деплоя remnawave/node)

1. Откройте панель RemaWave.
2. Откройте ноду (или создайте новую).
3. Найдите `SECRET_KEY` (может называться `secret`/`node_secret_key`).
4. Вставьте в fleet YAML:
   - `hosts.<alias>.remnawave.node_secret_key`.

Примечание: этот ключ нужен контейнеру ноды и не извлекается нашим API sync шагом.

### 2.2 `RW_PANEL_API_TOKEN` (для pre-step sync профилей и назначений нод)

1. В панели откройте раздел API/Access Tokens (название может отличаться в вашей версии UI).
2. Создайте токен с правами на:
   - чтение/изменение Config Profiles;
   - чтение/изменение Nodes.
3. Скопируйте токен и сохраните в GitHub Environment Secret `RW_PANEL_API_TOKEN`.

Если получили `401/403` в workflow, обычно причина в неверном токене или недостаточных правах токена.

### 2.3 `RW_PANEL_API_BASE_URL`

Это base URL панели, например:
- `https://panel.example.com`

Сохраните его в GitHub Environment Variable `RW_PANEL_API_BASE_URL`.
Скрипт сам добавит `/api`.

## 3) Какие поля добавились в fleet config

Для каждого host в `hosts.<alias>.remnawave`:
- `panel_node_uuid` — UUID ноды в панели (рекомендуется заполнять явно).
- `target_profile_name` — имя profile, который должен быть назначен ноде (если пусто: берётся alias хоста, например `de_node`).
- `inbound_tag` — базовый tag inbound (если пусто: `VLESS_<HOST_ALIAS>`, например `VLESS_DE_NODE`).
- `reality_target` — обычно `127.0.0.1:8443`.
- `reality_short_id` — shortId Reality (опционально; если пусто, генерируется автоматически и стабильно из `node_secret_key`).
- `reality_private_key` — privateKey Reality (опционально; если пусто, генерируется автоматически и стабильно из `node_secret_key`).
- `reality_server_name` — обычно ваш `caddy_domain`.

Если `panel_node_uuid` пустой, sync ищет ноду по имени `hosts.<alias>`, и только потом делает fallback по `ansible_host == node.address`.

## 4) Какие Secrets и Variables нужны в Environment

### Обязательные Secrets

1. `RW_FLEET_CONFIG_B64`
2. `ANSIBLE_SSH_PRIVATE_KEY`
3. `RW_PANEL_API_TOKEN`

### Опциональные Secrets

1. `RW_PROFILE_VARS_B64`
2. `ANSIBLE_VAULT_PASSWORD`

### Обязательные Variables

1. `RW_PANEL_API_BASE_URL`

### Опциональные Variables

1. `REMNAWAVE_API_ENDPOINT_TEMPLATE` (legacy источник runtime vars для роли `remnawave_node`).

## 5) Подготовка SSH-ключа для runner

Если ключа ещё нет:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ansible_actions -N ""
```

Секрет `ANSIBLE_SSH_PRIVATE_KEY` = содержимое `~/.ssh/ansible_actions`.

## 6) Как подготовить `RW_FLEET_CONFIG_B64`

1. Заполните `fleet.yml` по образцу.
2. Закодируйте:

macOS:
```bash
base64 -i fleet.yml | tr -d '\n'
```

Linux:
```bash
base64 -w 0 fleet.yml
```

3. Полученную строку сохраните в `RW_FLEET_CONFIG_B64`.

## 7) Как подготовить `RW_PROFILE_VARS_B64` (опционально)

Обычно этот секрет не нужен, потому что основные Reality значения берутся из fleet per-host.
Используйте его только если в шаблонах добавлены дополнительные глобальные placeholders.

Пример `profile-vars.json`:

```json
{
  "RW_REALITY_TARGET": "127.0.0.1:8443",
  "RW_REALITY_SERVER_NAME": "daring.watchd0g.dev"
}
```

Кодирование:

```bash
base64 -i profile-vars.json | tr -d '\n'
```

Сохраните результат в Secret `RW_PROFILE_VARS_B64`.

## 8) Загрузка через GitHub UI

1. GitHub -> `Settings` -> `Environments` -> нужное окружение (`Testing`/`Production`).
2. В `Environment secrets` добавьте:
   - `RW_FLEET_CONFIG_B64`
   - `ANSIBLE_SSH_PRIVATE_KEY`
   - `RW_PANEL_API_TOKEN`
   - опционально `RW_PROFILE_VARS_B64`, `ANSIBLE_VAULT_PASSWORD`
3. В `Environment variables` добавьте:
   - `RW_PANEL_API_BASE_URL`

## 9) Загрузка через `gh` CLI (альтернатива)

```bash
REPO="OWNER/REPO"
ENV_NAME="production"

openssl base64 -A -in fleet.yml | gh secret set RW_FLEET_CONFIG_B64 --repo "$REPO" --env "$ENV_NAME"
gh secret set ANSIBLE_SSH_PRIVATE_KEY --repo "$REPO" --env "$ENV_NAME" < ~/.ssh/ansible_actions
gh secret set RW_PANEL_API_TOKEN --repo "$REPO" --env "$ENV_NAME"
openssl base64 -A -in profile-vars.json | gh secret set RW_PROFILE_VARS_B64 --repo "$REPO" --env "$ENV_NAME"

gh variable set RW_PANEL_API_BASE_URL --repo "$REPO" --env "$ENV_NAME" --body "https://panel.example.com"
```

## 10) Запуск workflow

Рекомендуемый порядок:
1. `mode=bootstrap` для новых серверов.
2. `mode=lockdown` для этих же серверов.
3. `mode=deploy` для регулярных изменений.

Важные inputs:
- `limit`: `all` или `host1,host2`.
- `check_mode`: `true` для dry-run.
- `panel_sync_write`:
  - `false` = только отчёт рассинхронизации панели (read-only);
  - `true` = применить изменения профилей/назначений.

## 11) Проверка и безопасность

1. Не храните реальные IP/пароли/ключи в git.
2. Не коммитьте `hosts.ini`.
3. После успешного `bootstrap -> lockdown` смените bootstrap-пароли у провайдера.
4. Если `node_secret_key` пустой, значения всё равно сгенерируются детерминированно из host/profile данных, но предпочтительно держать `node_secret_key` заполненным для более стабильного и предсказуемого seed.
5. При ошибке `unknown panel_node_uuid` сверяйте UUID ноды в панели и fleet config.
