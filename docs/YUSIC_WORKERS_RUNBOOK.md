# Yusic Workers Runbook

Runbook для управления внешними `download-worker` нодами (`VPS + OpenWRT`) через relay-хост RU.

## 1) Модель деплоя

- Workflow: `.github/workflows/deploy-remnawave-node.yml`
- `target=yusic_worker`
- Runtime path:
  - GitHub runner подключается к relay-хосту (из `hosts` fleet config).
  - На relay выполняется `skopeo copy` нужного `sha-*` образа в `docker-archive`.
  - Relay отправляет архив и compose/env файлы на worker по SSH.
  - На worker: `docker load` + `docker compose up -d`.
- Auto-rollback:
  - при провале smoke роль `yusic_worker_rollback` возвращает предыдущий `WORKER_IMAGE`.

## 2) Fleet schema (RW_FLEET_CONFIG_B64)

Нужны секции:

```yaml
defaults:
  yusic_worker:
    relay_host_alias: tw-germ-1
    image_repo: ghcr.io/OWNER/Yusic_bot/download-worker
    enabled: true
    arch: arm64
    tags: ["provider:soundcloud", "region:nl", "cpu:low"]
    max_concurrent_jobs: 1
    network_mode: host
    dns: ["1.1.1.1", "8.8.8.8"]
    redis_url: redis://100.64.0.10:6379/0
    cache_bot_token: "<CACHE_BOT_TOKEN>"
    inline_cache_chat_id: "-1001234567890"
    # optional
    workdir: /opt/yusic-worker
    container_name: yusic_download_worker
    selfcheck_command: python services/download-worker/selfcheck.py

workers:
  wrt_me:
    enabled: true
    arch: arm64
    ssh:
      host: 100.112.10.20
      port: 22
      user: root
      # optional:
      # password: "..."
      # private_key: |
      #   -----BEGIN OPENSSH PRIVATE KEY-----
      #   ...
      #   -----END OPENSSH PRIVATE KEY-----
    tags: ["provider:soundcloud", "region:nl", "cpu:low"]
```

Ограничения v1:
- один `relay_host_alias` для всех enabled workers;
- deploy/update только по immutable `sha-*` image tag;
- `target=remnawave` path не затрагивается.

## 3) Workflow dispatch

### Bootstrap relay dependencies / connectivity check
- `target=yusic_worker`
- `worker_mode=bootstrap`
- `limit=all` или `limit=wrt_me,worker2`

### Deploy new worker image
- `target=yusic_worker`
- `worker_mode=deploy`
- `worker_image_tag=sha-<commit>`
- `limit=<worker_alias|all>`
- `run_smoke=true`

### Update existing worker image
- `target=yusic_worker`
- `worker_mode=update`
- `worker_image_tag=sha-<commit>`
- `limit=<worker_alias|all>`
- `run_smoke=true`

### Smoke only
- `target=yusic_worker`
- `worker_mode=smoke`
- `limit=<worker_alias|all>`

## 4) Smoke criteria

- container `yusic_download_worker` запущен;
- внутри контейнера проходит `python services/download-worker/selfcheck.py`;
- worker публикует heartbeat в Redis;
- coordinator видит worker в пуле.

## 5) Rollback behaviour

Если `worker_mode=deploy|update` и smoke не проходит:
- workflow запускает роль rollback;
- восстанавливается предыдущий `WORKER_IMAGE` из `yusic-worker.env`;
- выполняется `docker compose up -d`;
- workflow завершится ошибкой (это ожидаемо, чтобы зафиксировать инцидент).

## 6) Troubleshooting

1. Проверить relay:
- `docker --version`
- `skopeo --version`
- `ssh <worker>`

2. Проверить worker container:
- `docker ps`
- `docker logs yusic_download_worker --tail=200`

3. Проверить env внутри worker:
- `cat /opt/yusic-worker/yusic-worker.env`

4. Проверить heartbeat/coordinator:
- `GET /v1/workers` на coordinator.
