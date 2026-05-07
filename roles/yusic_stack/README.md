# `yusic_stack` — main yusic application stack

Renders `/opt/yusic/.env` and (re)starts the docker-compose miniapp-api +
telegram-cdn-relay services on the **polite** host (`89.23.98.20`, the host that
runs the miniapp-api consumer of `TELEGRAM_RELAY_URLS`).

## Why this role exists

Beads issue **yusic-pdh**: `/opt/yusic/.env` was hand-edited and got regenerated
on every redeploy from the GitHub Secret `DEPLOY_ENV_FILE` in
`Yusic_bot/.github/workflows/deploy.yml` (see lines 255-265 — the
`scp ... .env` step). That secret contains the dead Tailscale peer
`http://100.105.91.72:8091` (peer `honest`, offline since 2026-04) inside
`TELEGRAM_RELAY_URLS`. Every cache HIT in miniapp-api wasted ~5s on the dead
host before falling back to the live local relay `http://5.42.127.98:8091`.

This role makes Ansible the canonical writer of `/opt/yusic/.env`, with the
relay list driven by inventory variable `yusic_stack_telegram_relay_urls`.

## Migration plan

This role is shipped in three phases. Phase 1 is **scaffolding only** — applying
the playbook before completing Phases 2/3 will fight with the existing GHA
deploy.

### Phase 1 — this PR (Ansible_Servers)
- Add the role + skeleton inventory entry for `polite`.
- Land the `yusic.env.j2` template + vault placeholders.
- Document Phases 2/3.
- Run `--check --diff` against `polite` (NO write) to confirm template renders
  the same set of keys as the live file.

### Phase 2 — Yusic_bot follow-up PR
- In `.github/workflows/deploy.yml`, **remove** the "Copy .env from secret"
  step (lines 255-265) so the GitHub Action stops overwriting `.env`.
  Alternatively, keep the step but strip `TELEGRAM_RELAY_URLS=` from the
  `DEPLOY_ENV_FILE` secret content so it never appears in the deployed file.
- Add a regression guard: deploy fails if grep finds `100.105.91.72` in the
  rendered `.env`.

### Phase 3 — manual user actions
- **GitHub Secret rotation** (Yusic_bot repo settings):
  - Update `DEPLOY_ENV_FILE`: drop `100.105.91.72,` from `TELEGRAM_RELAY_URLS`.
  - This is the immediate workaround until Phase 2 lands.
- **Tailscale admin console** (https://login.tailscale.com/admin/machines):
  - Remove machine `honest` (Tailscale IP `100.105.91.72`, offline 36d).
  - No code change in this repo — Tailscale ACL is admin-console-managed.

## Vault setup (one-time)

```bash
# Create vault password file (do NOT commit)
openssl rand -hex 24 > ~/.ansible-vault-pass
chmod 600 ~/.ansible-vault-pass

# Materialise the operator-only vault from the .example template
cp group_vars/yusic_stack/vault.yml.example group_vars/yusic_stack/vault.yml
$EDITOR group_vars/yusic_stack/vault.yml   # paste real values

# Encrypt in place
ansible-vault encrypt --vault-password-file ~/.ansible-vault-pass \
    group_vars/yusic_stack/vault.yml
```

`vault.yml` is `.gitignored`; only the encrypted form ever exists on disk
near git, and the `.example` template is the only thing committed.

Add to `ansible.cfg`:

```ini
[defaults]
vault_password_file = ~/.ansible-vault-pass
```

For CI, mirror the contents of `~/.ansible-vault-pass` into a GitHub Secret
named `ANSIBLE_VAULT_PASSWORD` and write it to a file at workflow-start.

## Variables

See [`defaults/main.yml`](defaults/main.yml). The full list of secrets that
must be filled in `group_vars/yusic_stack/vault.yml`:

- `vault_yusic_stack_bot_token`
- `vault_yusic_stack_api_id`
- `vault_yusic_stack_api_hash`
- `vault_yusic_stack_yandex_token`
- `vault_yusic_stack_provider_token`
- `vault_yusic_stack_soundcloud_provider_token`
- `vault_yusic_stack_miniapp_secret_key`
- `vault_yusic_stack_redis_password`
- `vault_yusic_stack_cache_bot_token`

Read each value from the live `/opt/yusic/.env` on `polite` (over SSH alias
`polite_root` — `~/.ssh/config` already has the entry). Do **not** paste secret
values into PR descriptions, commit messages, or Claude-Code transcripts.

## Verification

After running:

```bash
ssh polite_root 'grep -E "^TELEGRAM_RELAY_URLS=" /opt/yusic/.env'
# Expected: TELEGRAM_RELAY_URLS=http://5.42.127.98:8091

ssh polite_root 'docker logs --tail 200 yusic_miniapp_api 2>&1 | grep STREAM_RELAY'
# Expected: only STREAM_RELAY_OK; no STREAM_RELAY_FAIL referencing 100.105.91.72.
```

Watch for at least 5 minutes of cache HITs to confirm the dead host doesn't
re-appear.

## Known gotchas

- The legacy `.env` is owned by `ernestsh:ernestsh 0600`. This role switches
  to `deploy:deploy 0640`. **Confirm the docker-compose containers actually
  run as `deploy`** before first apply — otherwise volume mounts/env-file
  reads may break. Check via:
  ```bash
  ssh polite_root 'docker inspect yusic_miniapp_api --format "{{.Config.User}}"'
  ssh polite_root 'docker compose -f /opt/yusic/docker-compose.release.yml config | grep user'
  ```
- The `.env.bak.*` files in `/opt/yusic/` still contain the dead host.
  Don't restore from them after this fix lands.
