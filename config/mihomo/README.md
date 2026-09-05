# mihomo routing config

`default.template.yaml` is the canonical copy of the MIHOMO `Default`
subscription template served by the panel at `ru.watchd0g.dev`
(uuid `1ef0346d-7989-4e50-88e6-bbe2b99672fc`). Routers download the rendered
form of it from `no.watchd0g.dev/<token>` as their OpenClash profile.

**This repository is the source of truth.** It did not used to be: until
2026-08-21 every fix was applied directly through the panel API, and the repo
files (`mihomo-remnawave-davoyan-rubypass-v2.0.yaml` and `-v2.1.yaml`, at the
repository root) were detached drafts that had drifted behind the live template
by two fixes. Those two files were deleted once this capture superseded them;
they survive in history at commit `cf2a013` if anyone needs to read them.
Editing the template through the panel web UI reintroduces that drift — change
this file instead.

**The web UI is not the only way to reintroduce it, and the worst way leaves no
trace on GitHub at all.** While the gate was being built, `DST-PORT,123,DIRECT`,
the NTP fake-ip-filter entries and the Kinopoisk CDN suffixes
(`trex.media`, `uma.media`) all reached the live panel without reaching this
file. They did not come from `.github/scripts/push_mihomo_template.py` as it
exists on `origin/master` — that revision appends none of them. They came from a
**local, unpushed revision** of that script (commits `91b48a3` and `7f9759e`,
present on one workstation's `master` and on no remote) run against the panel.

So for a stretch, production was defined by a script that existed on a single
machine: neither `origin/master` nor this template described what routers were
actually being served, and no diff anywhere on GitHub would have shown it. The
capture here was refreshed from the panel on 2026-08-24 to take those rules back
in — but that only closes the instance, not the hole.

Note also what the episode shows about the gate's reach: it guards the domains
listed in `routing-expectations.yaml`, not equality between this file and the
live template, so the drift passed a fully green run unnoticed. Pushing those
commits, reconciling the push script with this file, and adding a
drift-detection layer that compares the repo against the panel are Phase B work,
tracked as beads `as-3m8`.

It happened again before this branch merged, which is the point. Refreshed from
the panel on 2026-09-05: the live template had gained a `🏴‍☠️ WARP-ноды`
url-test group and turned `🏴‍☠️ Cloudflare WARP` into a `fallback` over
`[🏴‍☠️ WARP-ноды, 🌍 Зарубежные серверы (баланс)]`, plus a
`DOMAIN-SUFFIX,antigravity.google.com` entry in `google-warp-inline`. The WARP
change was pushed to the panel by commit `3d81269`, which lives on branch
`claude/gemini-all-servers-broken-d53eb4` and is **not on `origin/master`** —
so once more the live config was ahead of the default branch. Rationale for the
change itself: the WARP group resolved to a single WatchNet node with no
reserve, so when that node degraded (2026-07-23) Gemini/Antigravity/NotebookLM
timed out with nowhere to fall back to.

Capture procedure, for the next refresh: dispatch
`push-mihomo-template.yml` with `mode=inspect` (a GET only — it never PATCHes),
then copy the run's `live-mihomo-template-inspect` artifact over this file
verbatim. Note that in `apply` mode the artifact is written *before* the PATCH,
so it holds the pre-change template and must not be used as a capture.

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
