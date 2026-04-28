# Настройка секретов GitHub Actions (Fleet + RemaWave API Sync + OpenWrt)

Этот документ объясняет, как настроить деплой так, чтобы менять только Secrets/Variables в GitHub Environment, без коммитов `hosts.ini`.

## 1) Что вы редактируете

В обычной работе меняются только:
1. `RW_FLEET_CONFIG_B64` (секрет с описанием серверов).
2. Host-level Reality поля внутри fleet (`inbound_tag`, при необходимости `reality_short_id`/`reality_private_key`).
3. При необходимости `RW_PANEL_API_TOKEN` и `RW_PANEL_API_BASE_URL`.

Шаблоны:
- `fleet.two-servers.example.yml` — пример для 2 серверов.
- `fleet.two-nodes-plus-monitoring.example.yml` — пример для 2 нод + отдельного monitoring сервера.
- `fleet.example.yml` — общий multi-server шаблон.
- `fleet.openwrt.example.yml` — пример OpenWrt флота (LAN/ZT, Passwall2, probes).
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
2. `OPENWRT_FLEET_CONFIG_B64` (обязателен только для OpenWrt workflows)
3. `ANSIBLE_SSH_PRIVATE_KEY`
4. `RW_PANEL_API_TOKEN`
5. `TAILSCALE_AUTH_KEY` (обязателен, если включаете `feature_tailscale=true` для неавторизованных хостов)
6. `ZEROTIER_API_TOKEN` (обязателен для `deploy-openwrt` в режимах `deploy|lockdown`)

### Опциональные Secrets

1. `RW_PROFILE_VARS_B64`
2. `ANSIBLE_VAULT_PASSWORD`
3. `ALERT_TELEGRAM_TOPIC_ID_OPENWRT` (если OpenWrt алерты хотите в отдельный topic)
4. `ZEROTIER_NETWORK_ID` (default network id для OpenWrt ZT API sync; можно не задавать, если network_id указан per-host)

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

## 6.1) Как подготовить `OPENWRT_FLEET_CONFIG_B64`

1. Заполните `fleet.openwrt.example.yml` под ваши роутеры.
2. Закодируйте:

macOS:
```bash
base64 -i fleet.openwrt.yml | tr -d '\n'
```

Linux:
```bash
base64 -w 0 fleet.openwrt.yml
```

3. Полученную строку сохраните в `OPENWRT_FLEET_CONFIG_B64`.

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
   - `OPENWRT_FLEET_CONFIG_B64` (если используете OpenWrt workflows)
   - `ANSIBLE_SSH_PRIVATE_KEY`
   - `RW_PANEL_API_TOKEN`
   - `TAILSCALE_AUTH_KEY` (если используете `feature_tailscale=true`)
   - `ZEROTIER_API_TOKEN` (для OpenWrt read+authorize pre-step)
   - опционально `RW_PROFILE_VARS_B64`, `ANSIBLE_VAULT_PASSWORD`, `ALERT_TELEGRAM_TOPIC_ID_OPENWRT`
3. В `Environment variables` добавьте:
   - `RW_PANEL_API_BASE_URL`

## 9) Загрузка через `gh` CLI (альтернатива)

```bash
REPO="OWNER/REPO"
ENV_NAME="production"

openssl base64 -A -in fleet.yml | gh secret set RW_FLEET_CONFIG_B64 --repo "$REPO" --env "$ENV_NAME"
openssl base64 -A -in fleet.openwrt.yml | gh secret set OPENWRT_FLEET_CONFIG_B64 --repo "$REPO" --env "$ENV_NAME"
gh secret set ANSIBLE_SSH_PRIVATE_KEY --repo "$REPO" --env "$ENV_NAME" < ~/.ssh/ansible_actions
gh secret set RW_PANEL_API_TOKEN --repo "$REPO" --env "$ENV_NAME"
gh secret set TAILSCALE_AUTH_KEY --repo "$REPO" --env "$ENV_NAME"
gh secret set ZEROTIER_API_TOKEN --repo "$REPO" --env "$ENV_NAME"
openssl base64 -A -in profile-vars.json | gh secret set RW_PROFILE_VARS_B64 --repo "$REPO" --env "$ENV_NAME"

gh variable set RW_PANEL_API_BASE_URL --repo "$REPO" --env "$ENV_NAME" --body "https://panel.example.com"
gh variable set ZEROTIER_NETWORK_ID --repo "$REPO" --env "$ENV_NAME" --body "a84ac5c10a8906ee"
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

OpenWrt workflows:
- `deploy-openwrt` (режимы `bootstrap|deploy|lockdown`)
- `monitor-openwrt-fleet` (smoke + Telegram уведомления)

## 10.1) Sing-box proxy для воркеров (опционально)

Если у вас есть хост в стране, где нужный провайдер заблокирован (например,
RU → SoundCloud), включите на этом хосте локальный mixed-proxy через
`sing-box`, который туннелирует трафик в один из ваших VLESS-узлов.

Активация — через два поля в fleet:

```yaml
hosts:
  spb_node:
    ansible_host: 80.90.184.251
    features:
      feature_sing_box_proxy: true
    sing_box_proxy:
      route_final: nl-exit         # tag of the default outbound
      log_level: info
      outbounds:
        - type: vless
          tag: nl-exit
          server: nl.example.com
          server_port: 443
          uuid: <UUID>
          flow: xtls-rprx-vision
          tls:
            enabled: true
            server_name: nl.example.com
            utls: { enabled: true, fingerprint: firefox }
            reality:
              enabled: true
              public_key: "<reality-public-key>"
              short_id: "<short-id>"
```

Роль `sing_box_proxy` (tags=`[sing_box, proxy]`) сама поставит binary
(sha-pinned, см. `roles/sing_box_proxy/defaults/main.yml`), отрендерит
`/etc/sing-box/config.json` и выставит mixed inbound на `127.0.0.1:1080`.

Worker (`workers.<alias>`) на этом же хосте указывает на этот proxy через
`proxy.http_proxy: "http://127.0.0.1:1080"` — и его исходящий трафик к
провайдеру пойдёт через выбранный VLESS outbound.

Apply поэтапно (никак не трогая remnawave/caddy на хосте):

```bash
gh workflow run deploy-remnawave-node \
  --ref master \
  -f environment=production \
  -f target=remnawave \
  -f mode=deploy \
  -f tags=sing_box \
  -f limit=spb_node
```

После того как sing-box healthcheck зелёный — деплой воркера через
`target=yusic_worker -f worker_mode=deploy -f limit=worker_spb`.

## 11) Проверка и безопасность

1. Не храните реальные IP/пароли/ключи в git.
2. Не коммитьте `hosts.ini`.
3. После успешного `bootstrap -> lockdown` смените bootstrap-пароли у провайдера.
4. Если `node_secret_key` пустой, значения всё равно сгенерируются детерминированно из host/profile данных, но предпочтительно держать `node_secret_key` заполненным для более стабильного и предсказуемого seed.
5. При ошибке `unknown panel_node_uuid` сверяйте UUID ноды в панели и fleet config.
