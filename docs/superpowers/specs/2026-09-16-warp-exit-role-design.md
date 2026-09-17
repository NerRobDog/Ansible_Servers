# Роль `warp_exit`: WARP-выход нод Remnawave как код

**Дата:** 2026-09-16
**Эпик:** `as-prr`
**Статус:** дизайн согласован, не реализовано

---

## 1. Проблема

Выход Cloudflare WARP на нодах Remnawave ставился руками: на dh-germ-1 (WatchNet, `warp_mode: all`) и на tw-germ-1 (WatchNet-2, `warp_mode: inbound`). В репозитории нет ни одного упоминания `wgcf`, `wg-quick` или `wireguard`, а `roles/node_tuning/tasks/main.yml:20-21` прямо запрещает WARP сообщением «WARP is intentionally unsupported in v1».

При этом `remnawave.warp_mode` уже управляет профилем ноды в панели: при `all` выход по умолчанию — outbound `WARP`, который привязан к интерфейсу `warp` на хосте. Если переустановить такой хост и прогнать deploy, нода окажется зелёной в панели и в smoke-проверках, а весь клиентский трафик уйдёт в несуществующий интерфейс. Smoke WARP не проверяет.

Вторая, уже известная проблема — деградация бесплатной регистрации WARP. В июле 2026 регистрация dh-germ-1 после 1.48 ТиБ трафика теряла около 60% запросов через туннель при свежем handshake и чистом пинге до Cloudflare. Лечится только новой регистрацией, и это повторится. Заметить это сейчас нечем.

Цель: переустановка WARP-хоста поднимает WARP ролью без ручных шагов; нода не может подняться без работающего выхода; деградация регистрации приходит алертом в Telegram; перерегистрация выполняется одним запуском с откатом.

## 2. Принятые решения

| # | Вопрос | Решение | Почему |
|---|---|---|---|
| 1 | Где живёт регистрация WARP | Хост регистрируется сам при первом деплое; ключи в git и fleet не хранятся | Удерживать старую WARP-идентичность незачем, а свежая регистрация лечит известную деградацию. Выходной IP может сдвинуться внутри диапазона Cloudflare (июль: `104.28.197.9` → `.15`, тот же регион) |
| 2 | WARP не поднялся при деплое | Fail-closed для любого `warp_mode` кроме `none`: деплой хоста падает до `remnawave_node` | Оба WARP-хоста — единственные выходы Gemini; мёртвый `inbound` бьёт по тем же пользователям, что и мёртвый `all` |
| 3 | Хосты с WARP, поставленным руками | Роль забирает `warp.conf` под себя и собирает его из `wgcf-profile.conf` шаблоном, повторяющим текущий файл | Одна правда для всех хостов; перерегистрация перестаёт требовать ручного переноса ключей |
| 4 | Перерегистрация | Ручной тег `warp_reregister` с бэкапом и откатом | Решение «регистрация деградировала» остаётся за человеком; автоматическая смена выходного IP по флакающей метрике в деплой не пускается |
| 5 | Отчёты о деградации | Проба на хосте → textfile-коллектор node-exporter → правила Prometheus → существующий Alertmanager → Telegram | Ни нового бота, ни токенов на нодах; история в Grafana показывает медленный износ регистрации; `send_resolved` сообщит о восстановлении |
| 6 | Пороги | Два алерта: полный отказ 3 минуты и доля неудач выше 20% за 15 минут; плюс третий — проба перестала отчитываться | См. раздел 5 |
| 7 | Связь с переустановкой dh-germ-1 | Переустановка и bootstrap сейчас; полный deploy — уже с ролью | dh-germ-1 становится первым настоящим тестом «чистый хост → роль» |

По умолчанию, без отдельного обсуждения: `wgcf` фиксированной версии v2.2.32 со сверкой SHA256 из `checksums.txt` релиза; холды apt на `wireguard`/`wireguard-tools` роль не ставит и не снимает (обновление `wireguard-tools` туннель не перезапускает).

## 3. Живое состояние, которое роль обязана воспроизвести

Снято с tw-germ-1 2026-09-16:

- `wgcf` — `/usr/local/bin/wgcf`.
- `/etc/wireguard/wgcf-account.toml` (0644), `/etc/wireguard/wgcf-profile.conf` (0600), `/etc/wireguard/warp.conf` (0600).
- **Отличие, которое роль вносит намеренно:** `wgcf-account.toml` содержит токен доступа регистрации и сейчас читаем всеми пользователями хоста. Роль выставляет ему 0600. Это меняет только права, не содержимое, и не перезапускает туннель.
- Ключи в `warp.conf` совпадают с `wgcf-profile.conf` (сверено по хэшам).
- `warp.conf` отличается от профиля ровно так: убран IPv6-адрес из `Address`, убрана строка `DNS` (иначе wg-quick перепишет `resolv.conf`), добавлены `Table = off` в `[Interface]` и `PersistentKeepalive = 25` в `[Peer]`.

Итоговая форма `warp.conf`:

```ini
[Interface]
PrivateKey = <из профиля>
Address = <IPv4-адрес из профиля>
MTU = 1280
Table = off

[Peer]
PublicKey = <из профиля>
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = engage.cloudflareclient.com:2408
PersistentKeepalive = 25
```

`Table = off` критичен: туннель не трогает маршрутизацию хоста, в него попадает только трафик Xray, явно привязанный к интерфейсу `warp`.

## 4. Устройство роли

### 4.1. Состав

Роль `roles/warp_exit/`, запускается, когда эффективный `warp_mode` хоста не равен `none`.

| Файл | Назначение |
|---|---|
| `defaults/main.yml` | версия и SHA256 `wgcf`, пути, параметры проверки (попытки, пауза), параметры пробы |
| `tasks/main.yml` | порядок: install → register → configure → verify → probe; ветка `warp_reregister` |
| `tasks/install.yml` | `wireguard-tools` через apt; `wgcf` скачивается, только если версия или SHA256 на хосте не совпадают |
| `tasks/register.yml` | `wgcf register --accept-tos` и `wgcf generate` в `/etc/wireguard`, только если нет `wgcf-account.toml`; в check-mode не выполняется |
| `tasks/configure.yml` | читает `wgcf-profile.conf`, собирает `warp.conf`, включает `wg-quick@warp`, перезапускает только при изменении файла |
| `tasks/verify.yml` | fail-closed проверка туннеля |
| `tasks/reregister.yml` | перерегистрация с бэкапом и откатом |
| `tasks/probe.yml` | скрипт пробы, systemd service + timer, каталог textfile-коллектора |
| `filter_plugins/wgcf_profile.py` | фильтр `parse_wgcf_profile`: из текста профиля возвращает приватный ключ, ключ пира и IPv4-адрес; падает с понятной ошибкой, если чего-то нет |
| `templates/warp.conf.j2` | форма из раздела 3 |
| `files/warp-probe.sh` | скрипт пробы; параметры приходят через окружение из systemd-юнита, поэтому скрипт тестируется напрямую |
| `templates/warp-probe.service.j2`, `warp-probe.timer.j2` | systemd-юниты пробы |

Разбор профиля вынесен в filter-плагин ради тестируемости: регулярное выражение внутри Jinja в таске не покрыть юнит-тестом.

### 4.2. Место в плейбуке

В `playbook.yml` роль встаёт после `monitoring_stack` и перед `remnawave_node`. Если проверка WARP падает, Ansible прекращает обработку этого хоста, и `remnawave_node` для него не выполняется.

**Теги стоят на тасках роли, а не на её записи в `playbook.yml`.** Тег записи роли наследуется каждой таской, а таска с `never` пропускается, только пока не запрошен любой другой её тег. С `tags: [remnawave, warp]` на записи роли обычный `--tags warp` или `--tags remnawave` запустил бы перерегистрацию и сменил выходной IP. Импорты в `tasks/main.yml` помечены `[remnawave, warp]`, проба — ещё и `monitoring`, перерегистрация — только `[never, warp_reregister]`. Это закреплено тестом выбора тегов.

### 4.3. Обычный прогон

1. **Установка.** `wireguard-tools`; `wgcf` в `/usr/local/bin`.
2. **Регистрация.** Только при отсутствии `wgcf-account.toml`.
3. **Сборка `warp.conf`** из профиля через `parse_wgcf_profile` и шаблон. Права 0600. `wg-quick@warp` enabled; перезапуск — только при изменении файла, отдельной таской сразу после шаблона, а не handler: handler сработал бы в конце плея, уже после того как проверка оценила старый туннель.
4. **Проверка.** До 5 попыток с паузой 6 секунд; успех, когда одновременно у пира есть handshake (`wg show warp latest-handshakes` не ноль) и `curl --interface warp --max-time 5 https://www.cloudflare.com/cdn-cgi/trace` содержит `warp=on`. При неудаче таска падает с текстом, называющим хост и какое из условий не выполнено.
5. **Проба** — раздел 5.

Все таски, у которых в аргументах или выводе оказываются ключи, помечены `no_log: true`. Ключи не попадают ни в лог воркфлоу, ни в хвост лога Telegram-уведомления.

### 4.4. Перерегистрация (`tags=warp_reregister`)

Таски помечены `tags: [never, warp_reregister]`: Ansible выполняет их только при явном `--tags warp_reregister`, в обычном deploy они не запускаются никогда.

1. Бэкап `wgcf-account.toml`, `wgcf-profile.conf`, `warp.conf` в `/etc/wireguard/backup-<UTC-время>/`.
2. Старые `wgcf-account.toml` и `wgcf-profile.conf` удаляются, `wgcf register` и `wgcf generate` создают новые.
3. Пересборка `warp.conf`, перезапуск `wg-quick@warp`, проверка как в 4.3.
4. Блок `rescue` при любой неудаче шагов 2–3: файлы возвращаются из бэкапа, туннель перезапускается на старых ключах, таска падает с сообщением «перерегистрация не удалась, возвращена старая регистрация».

Хост в любом исходе остаётся либо на новой рабочей регистрации, либо на старой. Бэкапы роль не удаляет.

### 4.5. Check-mode

Регистрация не выполняется. У таски сборки `warp.conf` отключён дифф (`diff: false`): файл содержит приватный ключ, и дифф вывел бы его в лог. Поэтому критерий перед реальным прогоном — статус таски `Render WARP interface config`: `ok` означает, что роль не изменит уже работающий WARP, `changed` — стоп.

## 5. Проба и алерты

### 5.1. Проба

Скрипт `/usr/local/lib/warp-exit/warp-probe.sh`, systemd-таймер: `OnBootSec=60`, `OnUnitActiveSec=60`, service `Type=oneshot`.

Один прогон — 10 параллельных запросов `curl --interface warp --max-time 5`:

- 5 на `https://www.cloudflare.com/cdn-cgi/trace` — успех при ответе 200 с `warp=on` в теле;
- 5 на `https://www.gstatic.com/generate_204` — успех при ответе 204 (тот же путь, что у трафика Google/Gemini).

Результат пишется во временный файл в том же каталоге и переименовывается в `/var/lib/node_exporter/textfile/warp.prom`, чтобы node-exporter не прочитал недописанный файл. Интерфейс, цели и путь к `curl` задаются переменными окружения — это нужно для тестов.

### 5.2. Метрики

| Метрика | Тип | Значение |
|---|---|---|
| `warp_probe_success_ratio` | gauge | доля успешных запросов прогона, 0..1 |
| `warp_probe_latency_avg_seconds` | gauge | средняя задержка успешных; NaN, если успешных нет |
| `warp_status_on` | gauge | 1, если хотя бы один trace вернул `warp=on` |
| `warp_handshake_age_seconds` | gauge | секунды с последнего handshake пира |
| `warp_probe_last_run_timestamp_seconds` | gauge | unix-время завершения прогона |

### 5.3. node-exporter

В `roles/monitoring_agent/templates/docker-compose.yml.j2` к node-exporter добавляется `--collector.textfile.directory=/host/var/lib/node_exporter/textfile`. Корень хоста уже смонтирован как `/:/host:ro,rslave`, новых томов не требуется. На хостах без WARP каталог пуст, и коллектор ничего не отдаёт.

### 5.4. Правила

Новая группа `warp-exit` в `roles/monitoring_stack/templates/fleet-alerts.yml.j2`:

| Алерт | Выражение | for | Смысл для оператора |
|---|---|---|---|
| `WarpTunnelDown` | `warp_probe_success_ratio == 0` | 3m | туннель мёртв, выход не работает прямо сейчас |
| `WarpDegraded` | `avg_over_time(warp_probe_success_ratio[15m]) < 0.8` | 1m | регистрация изнашивается; запустить `warp_reregister` |
| `WarpProbeStale` | `time() - warp_probe_last_run_timestamp_seconds > 300` | 1m | проба перестала отчитываться |

Обоснование порогов: при июльской деградации отказывало 60% запросов, здоровый туннель дал 0 отказов из 19. Порог 20% ловит износ задолго до этого уровня; окно 15 минут не даёт всплескам по 20–30 секунд поднимать тревогу. Три минуты полного отказа — примерно время подъёма туннеля после ребута, так что штатный ребут алерт не вызывает.

`WarpProbeStale` нужен потому, что без него умерший таймер выглядит как здоровый WARP: метрика перестаёт обновляться, серия исчезает, и первые два алерта молча перестают срабатывать.

Доставка — существующим receiver `telegram` в `alertmanager.yml.j2`, тот же чат и топик, `send_resolved: true`. В аннотации `WarpDegraded` указывается команда запуска перерегистрации.

## 6. Правки вокруг роли

- **`.github/scripts/render-fleet-runtime.py`**: нормализация и валидация `remnawave.warp_mode` (`none|all|inbound`, по умолчанию `none`) по тем же правилам, что `normalize_warp_mode` в `remnawave-api-sync.py`. Неверное значение роняет рендер с сообщением, называющим хост. Иначе опечатка молча отключит роль, а синк с панелью упадёт — или наоборот.
- **`playbook.yml`**: факт `effective_warp_mode` из `fleet_host_cfg.remnawave.warp_mode`, условие роли `warp_exit`.
- **`roles/node_tuning`**: убрать проверку `remnawave_warp_enabled` и её упоминание в сообщении.
- **`group_vars/all.yml`, `group_vars/remnawave_node.yml`, `roles/node_tuning/defaults/main.yml`**: убрать `remnawave_warp_enabled`. Устаревшие ключи `warp_enabled`/`profile_preset` в fleet не читаются ни плейбуком, ни синком; их удаление из fleet — отдельная уборка, не в этом PR.
- **`.github/scripts/smoke-remnawave.sh`**: для хостов с `warp_mode` не `none` проверять `warp=on` через `curl --interface warp`.
- **Документация** по `docs/DOCUMENTATION_RULES.md`: `README.md`, `docs/OPERATIONS_GUIDE.md` (переустановка WARP-хоста, запуск `warp_reregister`, что делать по каждому из трёх алертов), `docs/ROLE_CATALOG.md` (новая роль, её параметры, связь с `warp_mode`).

## 7. Тесты

Molecule не используется для роли: в контейнере CI нельзя поднять WireGuard-интерфейс. Роль в molecule не запускается, потому что `warp_mode` по умолчанию `none`. Тесты пишутся до кода и запускаются в `ansible-ci.yml`.

| Тест | Файл | Проверяет |
|---|---|---|
| Валидация `warp_mode` | `.github/scripts/test-render-fleet-runtime.py` (дополнение) | `none/all/inbound` проходят, регистр и пробелы нормализуются, отсутствие даёт `none`, мусор падает с именем хоста |
| Разбор профиля | `.github/scripts/test-warp-exit-filter.py` | ключи и IPv4-адрес вытаскиваются из профиля в формате tw-germ-1; IPv6 и DNS игнорируются; профиль без ключа или без IPv4 даёт ошибку |
| Выбор тегов | `.github/scripts/test-warp-exit-tags.sh` | `--list-tasks` без тегов и с `warp`, `remnawave`, `monitoring` не содержит перерегистрации; с `warp_reregister` — содержит, и без пути установки |
| Шаблон `warp.conf` | `.github/scripts/test-warp-exit-template.py` | рендер с фейковыми ключами совпадает с эталонным файлом байт в байт (эталон повторяет форму раздела 3) |
| Скрипт пробы | `.github/scripts/test-warp-probe.sh` | поддельный `curl` в `PATH`: 10/10 успешных → ratio 1; 5/10 → 0.5; 0/10 → 0 и latency NaN; trace без `warp=on` не засчитывается; файл заменяется атомарно и валиден для формата textfile |
| Правила алертов | `.github/scripts/test-warp-alerts.yml` + шаг `promtool test rules` | 30-секундный провал не будит `WarpDegraded`; 20 минут при ratio 0.5 будят `WarpDegraded`; 3 минуты нуля будят `WarpTunnelDown`, 2 минуты — нет; остановка обновления timestamp будит `WarpProbeStale` |

`promtool` в CI скачивается фиксированной версии со сверкой SHA256. Шаблон `fleet-alerts.yml.j2` перед тестом рендерится в обычный YAML. Существующий шаг `ansible-playbook --syntax-check` покрывает подключение роли к плейбуку.

## 8. Выкат

1. PR → зелёный CI → разбор замечаний Codex/Copilot до мерджа.
2. **tw-germ-1, check-mode** (`check_mode=true`, `limit=tw-germ-1`, `tags=warp`): таска `Render WARP interface config` в статусе `ok`. Статус `changed` — стоп и правка шаблона, не хоста. Ожидаемо `changed` у установки `wgcf`: бинарник на tw-germ-1 не v2.2.32, замена файла туннель не трогает.
3. **tw-germ-1, реальный прогон** (`tags=warp,monitoring`): возраст handshake не сбрасывается, выход остаётся `104.28.197.9`, метрики `warp_*` появляются в node-exporter.
4. **ae-us-1** (`tags=monitoring`): группа `warp-exit` загружена, для tw-germ-1 ничего не горит.
5. **Остальные ноды** (`tags=monitoring`): флаг textfile-коллектора. Пересоздаётся только контейнер node-exporter.
6. **dh-germ-1** после переустановки и bootstrap: полный deploy. Чистый хост регистрирует WARP, проходит проверку, затем поднимается нода; через 15 минут алерты `warp-exit` для него не горят.

**Откат:** роль запускается только при `warp_mode` не равном `none`; откат PR возвращает прежнее поведение. На tw-germ-1 `warp.conf` по содержимому не меняется, на хосте откатывать нечего. Если после шага 5 node-exporter не стартует из-за флага — откат только изменения в `monitoring_agent`.

**Сознательно не делается:** живой тест алерта в Telegram остановкой туннеля на tw-germ-1 — это минимум 3 минуты без единственного живого выхода Gemini. Логику порогов покрывает `promtool`, сквозную доставку уже подтвердили живые алерты `FleetNodeExporterDown` про dh-germ-1.

## 9. Вне рамок

- Удаление устаревших ключей `warp_enabled`/`profile_preset` из fleet-конфига.
- Автоматическая перерегистрация.
- WARP на OpenWrt-роутерах.
- Платный WARP+ и лицензионные ключи.
