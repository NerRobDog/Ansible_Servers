# Ansible_Servers

Подготовка серверов для деплоя через Ansible с разделением по ролям:
- `base`: базовые системные пакеты.
- `docker`: установка Docker CE для Debian/Ubuntu и RHEL-compatible.
- `user_shell`: опциональное создание пользователя, SSH, sudo, pipx, zsh/oh-my-zsh.

## Структура

- `playbook.yml` — orchestration playbook.
- `group_vars/all.yml` — общие дефолты для всех хостов.
- `host_vars/<host>.yml` — хостовые профили (например, пользователь `ernestsh`).
- `roles/` — реализации ролей.
- `requirements.yml` — Ansible collections (включая `ansible.posix`).

## Быстрый старт

1. Установить коллекции:
   ```bash
   ansible-galaxy collection install -r requirements.yml
   ```
2. Установить python-инструменты (опционально, для lint):
   ```bash
   python3 -m pip install -r requirements.txt
   ```
3. Проверить синтаксис:
   ```bash
   mkdir -p .ansible/tmp
   ANSIBLE_LOCAL_TEMP=.ansible/tmp ANSIBLE_REMOTE_TEMP=.ansible/tmp \
     ansible-playbook -i hosts.ini playbook.yml --syntax-check
   ```
4. Запуск:
   ```bash
   ansible-playbook -i hosts.ini playbook.yml
   ```

## Molecule и CI

В репозитории добавлен сценарий Molecule: `molecule/default`.

Локальный запуск полного теста роли `user_shell`:

```bash
molecule test -s default
```

Требуется запущенный Docker daemon.

Что проверяется:
- создание тестового пользователя;
- установка `.zshrc` и Oh My Zsh;
- создание sudoers-файла;
- идемпотентность (через шаг `idempotence` в Molecule).

CI находится в [`.github/workflows/ansible-ci.yml`](./.github/workflows/ansible-ci.yml) и запускает:
- `ansible-playbook --syntax-check`;
- `ansible-lint`;
- `yamllint`;
- `molecule test -s default`.

## Модель переменных

По умолчанию создание пользователя отключено:
- `create_user: false` в `group_vars/all.yml`.

Чтобы включить пользователя только на конкретном хосте, используйте `host_vars/<host>.yml`.

Пример профиля:

```yaml
---
create_user: true
new_user: ernestsh
new_user_comment: ernestsh
new_user_home: "/home/{{ new_user }}"
new_user_groups:
  - wheel
  - docker
new_user_password_hash: null
new_user_authorized_keys:
  - "ssh-ed25519 AAAA..."
new_user_passwordless_sudo: true
```

## Важные флаги

- `new_user_authorized_keys_exclusive` (default: `true`):
  - `true` — Ansible управляет `authorized_keys` эксклюзивно.
  - `false` — добавляет ключи без очистки посторонних.
- `configure_root_zsh` (default: `true`) — настраивать ли zsh/oh-my-zsh для `root`.
- `enable_thefuck` (default: `false`) — добавить `thefuck` в pipx и список zsh-плагинов.
