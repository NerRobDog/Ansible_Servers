# mihomo routing config

`default.template.yaml` is the canonical copy of the MIHOMO `Default`
subscription template served by the panel at `ru.watchd0g.dev`
(uuid `1ef0346d-7989-4e50-88e6-bbe2b99672fc`). Routers download the rendered
form of it from `no.watchd0g.dev/<token>` as their OpenClash profile.

**This repository is the source of truth.** It did not used to be: until
2026-08-21 every fix was applied directly through the panel API, and the repo
files (`mihomo-remnawave-davoyan-rubypass-v2.*.yaml`) were detached drafts that
had drifted behind the live template by two fixes. Editing the template through
the panel web UI reintroduces that drift — change this file instead.

**The web UI is not the only way to reintroduce it.**
`.github/scripts/push_mihomo_template.py` appends rules to the live panel
template directly, and it drifted this file again while the gate was being
built: `DST-PORT,123,DIRECT`, the NTP fake-ip-filter entries and the Kinopoisk
CDN suffixes all reached the panel and not the repo. The capture here was
refreshed from the panel on 2026-08-24 to take them back in.

Note what that episode shows about the gate's reach: it guards the domains
listed in `routing-expectations.yaml`, not equality between this file and the
live template, so the drift passed a fully green run unnoticed. Reconciling the
push script with this file — and adding a drift-detection layer — is Phase B
work, tracked as beads `as-3m8`.

## Why it is excluded from yamllint

`.yamllint` ignores this directory. The file is a verbatim capture, and the
panel's formatting produces 51 yamllint findings. Reformatting it to satisfy the
linter would mean the repo no longer holds what the panel holds, which is the
one property this file exists to have.

## The template is not directly runnable

It carries a stub `proxies:` list and one Remnawave-specific key
(`remnawave:` inside proxy-groups) that the panel fills in per subscriber.
`include-all`, `exclude-filter` and `filter` are *native mihomo* fields and need
no translation. `.github/scripts/mihomo_candidate.py` assembles a runnable
config by splicing in real proxies and dropping the `remnawave:` key.

## Related

- `routing-expectations.yaml` — golden file asserting where each domain routes.
  It records one known hazard as-observed rather than as-desired:
  `no.watchd0g.dev`, the domain every router fetches its config from, currently
  falls through to the catch-all foreign group, so a broken proxy pool blocks the
  very fetch that would fix it — a circular dependency tracked as beads `as-6y4`.
  The inline comment in the file explains it.
- `docs/superpowers/specs/2026-08-21-mihomo-config-gate-design.md` — design.
