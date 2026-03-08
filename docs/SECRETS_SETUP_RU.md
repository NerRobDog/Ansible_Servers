# Настройка секретов GitHub Actions (Fleet YAML)

Этот документ описывает, как подготовить и загрузить секреты для деплоя без коммитов в `hosts.ini`.

## Что нужно заполнить

Используйте шаблон:
- [`fleet.two-servers.example.yml`](/Users/nik/Documents/PycharmProjects/Ansible_Servers/fleet.two-servers.example.yml) — готовый пример на 2 сервера.
- [`fleet.example.yml`](/Users/nik/Documents/PycharmProjects/Ansible_Servers/fleet.example.yml) — общий multi-server шаблон.

Критично заполнить:
1. `hosts.<alias>.ansible_host` — публичный IP.
2. `hosts.<alias>.bootstrap.username/password` — логин и пароль для первого входа (`mode=bootstrap`).
3. `hosts.<alias>.remnawave.node_secret_key` — секрет ноды.
4. `hosts.<alias>.remnawave.caddy_domain` — домен (если `feature_caddy_node=true`).

## Где взять значения для RemaWave

### Вариант A (рекомендуется для старта): вручную из панели RemaWave

1. Откройте вашу панель RemaWave.
2. Создайте ноду или откройте экран с параметрами существующей ноды.
3. Скопируйте `SECRET_KEY` (иногда может называться `node_secret_key` или просто `secret`).
4. Вставьте этот ключ в YAML:
   - `hosts.<alias>.remnawave.node_secret_key`.
5. При необходимости задайте свой порт:
   - `hosts.<alias>.remnawave.node_port` (обычно `3001`).

Примечание: названия разделов в UI панели могут немного отличаться между версиями, но нужен именно секрет ноды для `SECRET_KEY` контейнера.

### Вариант B: через API панели (опционально)

Используйте этот вариант, если хотите, чтобы playbook подтягивал параметры ноды автоматически.

Нужно:
1. GitHub Environment Variable `REMNAWAVE_API_ENDPOINT_TEMPLATE`  
   пример: `https://panel.example.com/api/nodes/{inventory_hostname}`.
2. GitHub Environment Secret `RW_PANEL_API_TOKEN`  
   это API-токен панели (обычно создается в разделе API/Access Tokens в панели).

Важно:
1. `RW_PANEL_API_TOKEN` нужен только для API-режима.
2. Если API не настроен, просто оставьте `RW_PANEL_API_TOKEN` пустым и заполняйте `node_secret_key` вручную в YAML.

## Какие секреты нужны в GitHub Environment

Обязательные:
1. `RW_FLEET_CONFIG_B64` — base64 от вашего YAML-конфига флота.
2. `ANSIBLE_SSH_PRIVATE_KEY` — приватный ключ, который runner использует для key-based SSH.

Опциональные:
1. `RW_PANEL_API_TOKEN`
2. `ANSIBLE_VAULT_PASSWORD`

## Шаг 1. Подготовить SSH-ключ для runner

Если ключа еще нет:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ansible_actions -N ""
```

Секрет `ANSIBLE_SSH_PRIVATE_KEY` = содержимое файла `~/.ssh/ansible_actions`.

Публичная часть (`~/.ssh/ansible_actions.pub`) будет автоматически прокинута на серверы в `mode=bootstrap`.

## Шаг 2. Закодировать YAML в base64

Пример с вашим файлом `fleet.yml`.

macOS:
```bash
base64 -i fleet.yml | tr -d '\n'
```

Linux (GNU coreutils):
```bash
base64 -w 0 fleet.yml
```

Универсально через OpenSSL (macOS/Linux):
```bash
openssl base64 -A -in fleet.yml
```

Результат (одна длинная строка) вставляется в секрет `RW_FLEET_CONFIG_B64`.

## Шаг 3. Загрузить секреты через GitHub UI

1. Откройте репозиторий на GitHub.
2. `Settings` -> `Environments` -> выберите окружение (например, `production`).
3. В блоке `Environment secrets` нажмите `Add secret`.
4. Добавьте:
   - `RW_FLEET_CONFIG_B64` (base64-строка из шага 2)
   - `ANSIBLE_SSH_PRIVATE_KEY` (приватный ключ целиком, включая строки `BEGIN/END`)
5. При необходимости добавьте `RW_PANEL_API_TOKEN` и `ANSIBLE_VAULT_PASSWORD`.

## Шаг 4. Загрузить секреты через gh CLI (альтернатива UI)

```bash
# Переменные
REPO="OWNER/REPO"
ENV_NAME="production"

# 1) RW_FLEET_CONFIG_B64
openssl base64 -A -in fleet.yml | gh secret set RW_FLEET_CONFIG_B64 --repo "$REPO" --env "$ENV_NAME"

# 2) ANSIBLE_SSH_PRIVATE_KEY
gh secret set ANSIBLE_SSH_PRIVATE_KEY --repo "$REPO" --env "$ENV_NAME" < ~/.ssh/ansible_actions
```

## Как запускать workflow

Рекомендуемая последовательность для новых серверов:
1. `mode=bootstrap` — вход по паролю, установка SSH-ключа.
2. `mode=lockdown` — отключение password SSH и root SSH login.
3. `mode=deploy` — обычные последующие деплои по ключу.

`limit` можно ставить:
- `all` — все хосты,
- `de_node,nl_node` — только часть флота.

## Важные правила безопасности

1. Не коммитьте реальные IP/пароли/секреты в git.
2. Не храните `hosts.ini` в репозитории.
3. Держите bootstrap-пароли только в `RW_FLEET_CONFIG_B64` внутри Environment Secret.
4. После первого успешного цикла `bootstrap -> lockdown` можно сменить bootstrap-пароли на стороне провайдера.
