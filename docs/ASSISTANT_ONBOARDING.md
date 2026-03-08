# Assistant Onboarding

Документ для нового помощника/инженера, который впервые работает с этим репозиторием.

## 1) С чего начать

1. Прочитать:
   - `README.md`
   - `docs/OPERATIONS_GUIDE.md`
   - `docs/ROLE_CATALOG.md`
   - `docs/DOCUMENTATION_RULES.md`
2. Понять, что source of truth по серверам — это GitHub Environment Secret `RW_FLEET_CONFIG_B64`.
3. Понять, что новые серверы добавляются без push в git.

## 2) Базовые правила работы

- Не хранить пароли/ключи в git.
- Не менять контракты секретов без обновления документации.
- Не включать `lockdown`, пока не проверен вход по SSH-ключу deploy-user.
- Всегда запускать проверки перед merge.

## 3) Как понять, что делает конкретный хост

Смотрите блок `features` и `custom_roles` в fleet-конфиге:
- `feature_base`, `feature_docker` — базовый слой.
- `feature_remnawave_node`, `feature_caddy_node`, `feature_node_tuning` — node-слой.
- `feature_user_shell` — shell/user provisioning.
- `custom_roles` — проектные расширения.

## 4) Типовой change-process

1. Изменили роль/переменную/workflow.
2. Обновили документацию.
3. Прогнали локальные проверки.
4. Описали в PR:
   - что изменилось;
   - какие риски;
   - как откатить.

## 5) Checklist перед релизом

- [ ] `python .github/scripts/test-render-fleet-runtime.py` проходит
- [ ] `ansible-playbook --syntax-check` проходит
- [ ] `ansible-lint` проходит
- [ ] `yamllint` проходит
- [ ] README и docs обновлены
- [ ] Примеры fleet-конфига актуальны
