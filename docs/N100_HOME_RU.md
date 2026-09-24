# N100 Home Server — практическая инструкция

Документ для домашнего N100 (Ubuntu 24.04, IP `192.168.5.134`, ZeroTier-IP `10.147.20.47`),
раскатанного ролями из этой репы. Покрывает: доступ к сервисам, как поднимается Kodi-kiosk,
и как заменить Kodi на обычный Ubuntu-desktop.

## 1. Доступ к сервисам

### Сводка портов

| Сервис | URL | Порт | Где гейтится |
|---|---|---|---|
| Jellyfin | `http://n100:8096` | 8096 | LAN + ZT |
| Jellyfin HTTPS | `https://n100:8920` | 8920 | LAN + ZT |
| Obico (3D-надзор) | `http://n100:3334` | 3334 | LAN + ZT |
| Sunshine (стрим) | `https://n100:47990` | 47984/47989/48010 TCP, 47998-48010 UDP | LAN + ZT |
| Kodi web (JSON-RPC) | `http://n100:8080` | 8080 | LAN + ZT |
| node_exporter | `n100:9100` | 9100 | внутреннее |
| cAdvisor | `n100:8080` | 8080 | конфликт с Kodi — см. §6 |
| rclone serve | по конфигу `instances` | per-instance | LAN + ZT |

### Можно ли вместо IP использовать хостнейм?

**Да, тремя способами в порядке нарастания «правильности»:**

#### A. mDNS / Bonjour (`n100.local`) — простейший

На Ubuntu Avahi обычно установлен и работает из коробки. Проверь:
```bash
ssh -i ~/.ssh/ansible_actions deploy@192.168.5.134 'systemctl is-active avahi-daemon'
```
Если `active` — `n100.local` уже резолвится на macOS, iOS, большинстве Linux. Проверка с
маковского терминала:
```bash
ping n100.local
```
**Подводный камень:** mDNS не пересекает сегменты сети. Через ZeroTier `n100.local` НЕ
будет работать — только в локалке. Для ZT см. вариант B.

#### B. ZeroTier Central DNS (`n100.<zt-net>`) — для удалёнки

В my.zerotier.com → Networks → твоя сеть → DNS:
- Search Domain: `home` (или что хочешь)
- Server Address: `10.147.20.1` (любой IP в подсети сети, можно пустой)

Затем в Members присвой N100 имя `n100`. Через ZT можно будет ходить как `n100.home`.

Альтернатива без Central DNS — просто прописать `10.147.20.47 n100` в `/etc/hosts` на
устройствах, которые ходят к N100 через ZT.

#### C. Локальный DNS на роутере / Pi-hole / unbound — самый чистый

Если у тебя на роутере/openwrt есть DNS — добавь A-запись `n100 → 192.168.5.134` в
домашней зоне. Тогда `n100.home.lan` или просто `n100` будет резолвиться на любом
устройстве в LAN независимо от mDNS. У тебя openwrt-плейбуки в репе есть, можно
добавить запись в `roles/openwrt_network_core` если решишь автоматизировать.

#### Что я бы сделал

`n100.local` через mDNS для домашних устройств + добавить `10.147.20.47 n100` в
`/etc/hosts` на ноуте/телефоне для ZT. Потратишь 30 секунд — будет работать всегда.

### Использование URL после настройки имени

После `n100.local` или `n100` все URLы из таблицы выше можно переписать как
`http://n100.local:8096`, `http://n100:3334` и т.д.

Также имеет смысл прописать в `fleet.home.yml`:
```yaml
jellyfin_published_url: "http://n100.local:8096"
obico:
  site_url: "http://n100.local:3334"
```
И прогнать роли с `--tags jellyfin,obico`. После этого Jellyfin/Obico будут давать
правильные ссылки в письмах и DLNA-каталогах.

---

## 2. Как запускается Kodi (kiosk)

### Стек

```
boot
 └─ systemd graphical.target
     └─ greetd (display manager) — слушает VT1
         └─ autologin как deploy
             └─ cage (Wayland-композитор для одного приложения)
                 └─ kodi --standalone
```

### Что это значит на практике

1. Подключаешь N100 по HDMI к ТВ → загрузился Ubuntu → автоматически логинится
   `deploy` без пароля.
2. На экране сразу появляется Kodi на весь экран. Ничего вокруг (панелей, обоев) нет —
   `cage` — это «один Wayland-приложение на весь дисплей и точка», специально для
   kiosk-сценариев.
3. Если выйти из Kodi (`Power → Exit`) — `cage` завершается, `greetd` стартует сессию
   заново, Kodi снова на экране через ~3 секунды. То есть из kiosk выйти нельзя, можно
   только перезагрузить.

### Управление

- **Клавиатура/мышь по USB** — работают сразу.
- **HDMI-CEC** — пакет `cec-utils` + `libcec6` поставлены ролью. Чтобы пультом ТВ
  управлять Kodi:
  - Settings → System → Input → Peripherals → CEC Adapter → Enable.
  - На большинстве ТВ кнопки управления HDMI-устройствами (часто красная/зелёная или
    отдельный режим Anynet+ / Bravia Sync / Simplink) перенаправят навигацию в Kodi.
- **Kodi Remote (мобильное приложение)** — на iOS/Android есть «Kore» (Android-only,
  официальный) и «Kodi Remote» (iOS, неофициальный). Подключение по адресу `n100.local:8080`
  (Kodi web, мы её включили в advancedsettings.xml).

### YouTube с телефона на ТВ — как это устроено

Кнопка каста в мобильном YouTube использует **две** связки:
1. **DIAL поверх SSDP** — телефон шлёт мультикаст на `239.255.255.250:1900` и ждёт
   ответа от устройства, которое представится Chromecast'ом.
2. **Cast V1** — обнаруженное устройство принимает сессию управления плеером
   (play/pause/queue/volume) на TCP 8008/8009.

Сам YouTube-аддон Kodi на SSDP **не отвечает**. Нужен прокси-аддон
**`script.tubecast`** (автор enen92) — он эмулирует Chromecast V1, ловит SSDP-запросы,
принимает Cast-сессию и проксирует команды в `plugin.video.youtube`, который уже
выступает плеером. Решение работает потому что Google всё ещё держит обратную
совместимость с Cast V1.

#### Шаги настройки

1. **Разреши YouTube-аддон.** Kodi → Add-ons → My add-ons → Video add-ons →
   **YouTube** → Enable. Авторизация через `youtube.com/activate` нужна только если
   хочешь свои подписки/плейлисты — для самого каста не обязательна.

2. **Разреши TubeCast.** Kodi → Add-ons → My add-ons → **Program add-ons** →
   **TubeCast** → Enable. (Он именно в Program, не в Video — частая точка
   спотыкания.)

3. **Запусти TubeCast хотя бы один раз вручную.** Главное меню → Programs →
   TubeCast. Сервис поднимется резидентно и начнёт отвечать на SSDP. После первого
   запуска стартует автоматически вместе с Kodi.

4. **Имя устройства**, которое увидит телефон, берётся из System.FriendlyName
   (Settings → System → System info → System name). По умолчанию = hostname,
   у нас сейчас `n100`. Если хочешь поменять — там же можно. В настройках TubeCast
   также есть отдельное поле «Kodi advertisement name» как fallback.

5. **L2-подсеть и мультикаст.** DIAL/SSDP идёт мультикастом, поэтому телефон и Kodi
   должны быть в одном Wi-Fi/Ethernet сегменте, а на роутере/свиче не должно быть
   AP-isolation или IGMP snooping без querier'а. На свиче `TL-SG108E` снупинг
   часто включён — выключи в его веб-морде, либо подними querier на роутере. Через
   ZeroTier каст работать НЕ будет (мультикаст по ZT по умолчанию не пробрасывается
   между подсетями).

6. **Открой YouTube на телефоне** → должна появиться иконка каста → в списке выбери
   `n100`. Дальше телефон отдаёт Kodi только видео-ID, а HD-поток Kodi тянет
   напрямую с YouTube через свой плагин.

#### Если не появляется

```bash
# Проверь что TubeCast слушает SSDP
ssh deploy@n100.local 'sudo ss -ulnp | grep -E "1900|8008|8009"'

# Проверь что мультикаст-пакет от телефона приходит на N100
ssh deploy@n100.local 'sudo tcpdump -i any -n udp port 1900 -c 5'
# с телефона жми «Cast» в YouTube — должны посыпаться M-SEARCH запросы.
# Если нет — проблема в L2 (AP isolation, IGMP snooping без querier).
```

### Торренты (Elementum)

Аддон `plugin.video.elementum` поставлен в `/home/deploy/.kodi/addons/`. Чтобы он стал
видим в меню — Kodi → Add-ons → My add-ons → Video add-ons → Elementum → Enable. Затем
один раз настрой:
- Library path: `/media` (туда монтируются USB-диски через `usb_automount`)
- Sequential downloads: ON (по умолчанию уже включено в этом аддоне)

### Если Kodi падает или зависает

```bash
ssh deploy@n100.local
sudo systemctl restart greetd
```
Kiosk перезапустится за 3-5 секунд.

---

## 3. Если хочешь обычный Ubuntu-desktop вместо Kodi

Полностью валидный сценарий — N100 как HTPC с привычным окружением, а Kodi/Jellyfin
запускаешь как обычные приложения (или вообще не запускаешь).

### Вариант 1 — отключить kiosk, поставить GNOME

В `fleet.home.yml`:
```yaml
features:
  feature_tv_kiosk: false
```

Затем выключи greetd и поставь обычный desktop:
```bash
ssh deploy@n100.local

# 1. Отрубить kiosk-стек
sudo systemctl disable --now greetd
sudo apt purge -y greetd cage seatd

# 2. Поставить полноценный Ubuntu Desktop
sudo apt install -y ubuntu-desktop

# 3. Включить графический менеджер по умолчанию
sudo systemctl enable gdm3
sudo systemctl set-default graphical.target
sudo reboot
```

После ребута получишь обычный логин-экран GDM. Залогинься как `deploy`. Внутри уже
доступны:
- **Kodi** — просто запусти из меню приложений как обычную программу. Все аддоны и
  настройки которые накатил плейбук (`/home/deploy/.kodi/`) останутся в силе.
- **Firefox/Chrome** — уже в составе ubuntu-desktop. YouTube/Twitch как обычная вкладка.
- **Jellyfin Media Player** — если хочешь нативный JF-клиент:
  ```bash
  curl -L -o jmp.deb "https://github.com/jellyfin/jellyfin-media-player/releases/latest/download/jellyfin-media-player_amd64.deb"
  sudo apt install -y ./jmp.deb
  ```
- **Sunshine** уже есть, его веб-морда `https://n100.local:47990` работает независимо от
  desktop'а.

### Вариант 2 — лёгкий desktop без GNOME

GNOME на N100 крутится, но прожорлив. Альтернативы попроще:
```bash
# Xfce — ~150 MB RAM в простое
sudo apt install -y xubuntu-desktop

# или ещё легче — LXQt
sudo apt install -y lubuntu-desktop
```
Дальше тот же `systemctl enable gdm3` (Xubuntu может ставить lightdm — оба ок).

### Вариант 3 — гибрид: Ubuntu desktop, но автостарт Kodi при включении

Удобный сценарий: «обычно компьютер, но если включил из режима ожидания → сразу Kodi
на ТВ». 

1. Поставь ubuntu-desktop как в варианте 1, но **не сноси** cage/greetd.
2. В fleet.home.yml оставь `feature_tv_kiosk: true` — тогда плейбук конфликта не создаст,
   но активным будет greetd.
3. Чтобы менять режим:
   ```bash
   # Режим Kodi
   sudo systemctl disable --now gdm3 && sudo systemctl enable --now greetd

   # Режим Desktop
   sudo systemctl disable --now greetd && sudo systemctl enable --now gdm3
   ```
   Можно повесить на скрипт + кнопку.

### Вариант 4 — поднять обе сессии в GDM

GDM 46+ умеет мульти-сессии. Если оставишь и `gdm3`, и Kodi-аддоны, можно через
`/usr/share/wayland-sessions/kodi.desktop` добавить Kodi-standalone как опцию выбора
сессии в логин-меню. Тогда выбираешь либо «Ubuntu», либо «Kodi» при логине. Это уже
ручной патч, ролью не делаю — скажи если нужно автоматизировать.

---

## 4. Day-2 операции

### Перезапуск раскатки

Все роли идемпотентны — гонять можно сколько угодно раз:
```bash
cd ~/Documents/PycharmProjects/Ansible_Servers
source .venv/bin/activate
set -a; source .env; set +a
ansible-playbook playbook.yml -i hosts.home.ini --extra-vars "@fleet.home.yml"
```

### Точечно одна роль

```bash
ansible-playbook playbook.yml -i hosts.home.ini --extra-vars "@fleet.home.yml" \
  --tags jellyfin
```
Доступные теги: `zerotier`, `usb_automount`, `rclone`, `jellyfin`, `sunshine`, `obico`,
`tv_kiosk`, `monitoring`, `base`, `docker`, `firewall`.

### Добавить rclone-инстансы (после копирования `rclone.conf` со старой машины)

```yaml
# в fleet.home.yml внутри hosts.n100.rclone_serve:
config: |
  [gdrive]
  type = drive
  scope = drive
  token = {"access_token":"..."}
  ...
instances:
  - name: gdrive-webdav
    protocol: webdav
    remote: "gdrive:"
    port: 8089
    user: deploy
    pass: "{{ lookup('env', 'RCLONE_WEBDAV_PASSWORD') }}"
  - name: media-sftp
    protocol: sftp
    remote: /media
    port: 2022
```
Затем:
```bash
export RCLONE_WEBDAV_PASSWORD='...'
ansible-playbook ... --tags rclone
```
Каждый инстанс — отдельный systemd-юнит `rclone-serve@<name>.service`. Логи в
`/var/log/rclone/<name>.log`.

### USB-диск воткнул, что дальше

Ничего. Через 1-3 секунды после подключения udev сделает:
1. Опознает FS (vfat/exfat/ntfs/ext4/btrfs/...).
2. Смонтирует в `/media/<LABEL>` (или `/media/disk-<UUID>` если метки нет).
3. Назначит владельцем `deploy:deploy`.
4. rclone-инстансы серверующие `/media` сразу увидят содержимое.

Проверить:
```bash
ssh deploy@n100.local 'lsblk -o NAME,MOUNTPOINT,FSTYPE,LABEL'
```

### Obico — добавить принтер

1. Зайди на `http://n100.local:3334`, создай админский аккаунт.
2. Add Printer → выбери свой стек (OctoPrint / Klipper-Moonraker).
3. Obico даст одноразовый код `xxxx-xxxx`. Введи его в плагин на принтере.
4. Линк страницы Obico в плагине = `http://n100.local:3334`.

### ZeroTier — добавить новую ноду

В fleet.home.yml уже есть `zerotier_network_id`. На новой машине просто пропиши тот же
ID, токен в env, прогон роли. После запуска Central API авторизует ноду автоматически.

---

## 5. Sunshine — стрим экрана с/на ТВ

Sunshine на N100 — **сервер**. Клиент — Moonlight на ноуте/телефоне/планшете.

### Как заработает

Sunshine ждёт активную графическую сессию (он юзерский systemd-юнит, привязанный к
`graphical-session.target`). Поэтому он стартует **только когда залогинен deploy через
greetd / gdm3**. На headless-N100 (без HDMI) sunshine не поднимется.

### Pairing

1. Открой `https://n100.local:47990` (self-signed cert, прими предупреждение).
2. Логин/пароль создаются при первом заходе.
3. Add Client → даёт PIN. Вводишь PIN в Moonlight на ноуте — клиент привязан.

### Что можно стримить

- Десктоп на ТВ → запусти Moonlight на ноуте, выбери N100, экран ноута идёт на ТВ
  через N100 (но это редкий случай — обычно делают наоборот).
- Десктоп N100 на ноут → реально удобно: сидишь с ноутом, удалённо рулишь Kodi/всем чем
  угодно на N100 с низкой задержкой (5-15 ms на LAN, 50-100 ms через ZT).
- Игры — да, тоже работает, но N100 без дискретки сильно не разгуляешься.

---

## 6. Известные конфликты + куда смотреть, если что

### Порт 8080 — Kodi web vs cAdvisor

И Kodi web JSON-RPC, и cAdvisor (часть `monitoring_agent`) хотят 8080. Сейчас
`feature_monitoring_agent: true` → cAdvisor возьмёт 8080. Если включаешь Kodi web для
Kore-приложения, либо смени порт Kodi в `roles/tv_kiosk/templates/advancedsettings.xml.j2`,
либо смени cAdvisor через `monitoring_agent_cadvisor_port` в fleet config.

### N100 iGPU — драйверы

Установлен `intel-media-va-driver-non-free` (нужен для QSV-транскода). Проверь что VAAPI
видит карту:
```bash
ssh deploy@n100.local 'docker exec jellyfin vainfo 2>&1 | head -20'
```
Должны быть строки `VAProfileH264*`, `VAProfileHEVC*`. Если нет — открой issue.

### ZeroTier — нода REQUESTING_CONFIGURATION после ребута

Бывает при холодном старте если ZT-демон стартует раньше сети. Лечится одной командой:
```bash
sudo systemctl restart zerotier-one
```
Если повторяется — добавь `Wants=network-online.target` в drop-in юнита.

### Логи

```bash
# Jellyfin/Obico
docker logs -f jellyfin
docker logs -f obico-server-web-1

# rclone serve
sudo journalctl -u rclone-serve@<name>.service -f

# Kiosk
sudo journalctl -u greetd -b

# ZeroTier
sudo journalctl -u zerotier-one -f
```

---

## Контакты репы

Багрепорты / запросы новых ролей — issues в `NerRobDog/Ansible_Servers`.
PR: https://github.com/NerRobDog/Ansible_Servers/pull/33
