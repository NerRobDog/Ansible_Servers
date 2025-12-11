# Ansible_Servers

## Пользовательские переменные
- Для каждого пользователя создайте отдельный файл в каталоге `vars/users/`, скопировав шаблон `vars/users/example.yml.example`
и удалив расширение `.example` (например, `vars/users/ivan.yml`).
- Старый путь с `vars/ernestsh.yml` больше не используется и его можно удалить.
- В каталоге `vars/users/` может быть несколько профилей, но плейбук подключает ровно один. Укажите его явным образом
  через `-e "user_vars_file=vars/users/ivan.yml"` или задайте переменную `user_vars_file` в inventory.

## Запуск
```
ansible-playbook -i hosts.ini playbook.yml
```
