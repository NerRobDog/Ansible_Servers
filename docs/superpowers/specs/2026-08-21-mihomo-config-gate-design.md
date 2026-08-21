# mihomo config as code: validation gate + canary auto-rollout

**Date:** 2026-08-21
**Epic:** `as-5g2`
**Related:** `as-7oj` (site-availability monitoring — supplies the router probes this design reuses)
**Russian version:** [2026-08-21-mihomo-config-gate-design.ru.md](2026-08-21-mihomo-config-gate-design.ru.md) — keep both in sync when editing
**Status:** design agreed, not yet implemented

---

## 1. The precondition nobody knew about

The request assumes the repository holds the config that routers run. It does not.

The live MIHOMO `Default` template in the panel (`1ef0346d-7989-4e50-88e6-bbe2b99672fc`)
was pulled and compared structurally against `mihomo-remnawave-davoyan-rubypass-v2.2.yaml`:

```
top-level keys      identical
proxies, dns, tun, sniffer, profile, …    SAME
proxy-groups        DIFF
rule-providers      DIFF
rules               DIFF
```

The live template is **ahead** of the repo:

- two proxy-groups exist only live — `📺 YT фон / PiP` and `📺 YT без рекламы`, the
  2026-08-18 YouTube fix that was applied through the API;
- four groups differ — `🎮 Игры`, `💬 Discord`, `📺 YouTube`, `🤖 ChatGPT и AI`;
- one rule exists only live — `IP-CIDR,193.233.75.48/32,DIRECT,no-resolve`, the
  de-matrix-1 bypass added for the TURN experiment.

The cause is structural, not sloppiness: `push_mihomo_template.py` is not a file
uploader. It fetches the live template, reorders the YouTube group and patches it
back. Every fix for months has landed directly in the panel while the repo files sat
as detached drafts. The live template even carries a stale comment claiming it "was
never pushed to Remnawave".

**Consequence:** building push-to-deploy on top of the current repo file would, on its
very first run, silently revert the YouTube fix and the TURN bypass.

**Decision: reconcile first.** Commit the live template as the canonical file, move
`v2.0`/`v2.1`/`v2.2` into `archive/`, and only then wire up the pipeline. The first
diff the gate ever produces must be honest.

---

## 2. How a candidate config is tested without touching the panel

The template is not directly runnable — it carries Remnawave extensions
(`remnawave: {include-proxies: false}`) and a stub `proxies:` list that the panel
fills per subscriber.

**Verified experimentally on 2026-08-21**, and this is the load-bearing finding of
the whole design: `include-all`, `exclude-filter` and `filter` are *native mihomo*
proxy-group fields, not Remnawave extensions. The only panel-specific key is
`remnawave:`. So a runnable candidate can be assembled entirely offline:

1. take the candidate template;
2. replace its stub `proxies:` with the real proxy list from a rendered subscription
   (a plain read-only `GET` against `no.watchd0g.dev/<token>` with a `clash-meta`
   user agent — 9 proxies, 20 groups, 34 rules, no `remnawave` keys);
3. drop the `remnawave:` key from every group.

`mihomo -t` on the result answers `configuration file test is successful`. Zero panel
side effects, no template ever goes live to be tested.

### 2.1 Harness requirements found by hitting them

Three failures that are silent or misleading if not handled:

- **`tun` must be disabled in the harness.** It needs `NET_ADMIN` and a real tun
  device; the test runs userspace-only.
- **`allow-lan: false` must be overridden.** With it, mihomo listens only on loopback
  *inside* the container, so a published port goes nowhere and curl fails in 3 ms with
  `http=000` — which reads like a config error and is not one.
- **Assertions must key on the rule match, not the HTTP status.** `chatgpt.com`
  returned **403** through the US node: Cloudflare rejecting a VPS address. A gate
  keyed on HTTP 200 would go red for reasons unrelated to the config under test.

### 2.2 Routing is observable exactly

With the harness running, requests through the mixed port produce parseable
attribution — verified live:

```
--> www.youtube.com:443  match RuleSet(youtube)       using 📺 YouTube[🇳🇱 Netherlands-2]
--> chatgpt.com:443      match RuleSet(openai-inline) using 🤖 ChatGPT и AI[🇺🇸 USA-2]
--> yandex.ru:443        match RuleSet(ru-inline)     using ⚪🔵🔴 RU сайты[🇷🇺 Без VPN]
```

domain → matched rule → group → concrete node. The Clash API `/connections` endpoint
gives the same as a `chains` array. A full run takes about ten seconds.

---

## 3. The gate

Four layers, each catching a class the previous cannot:

1. **Syntax** — `mihomo -t` on the assembled candidate.
2. **Referential integrity** — every rule targets a group that exists; every
   `RULE-SET` names a declared provider; every provider URL returns 200; every regex
   in `exclude-filter`/`filter` compiles; no orphan groups. This is the class where a
   renamed group leaves a dangling rule.
3. **Routing** — run the harness, drive the whole domain list, record
   domain → rule → group.
4. **Regression** — compare that map against the expectations file. This is the layer
   that answers "I fixed YouTube — did I break Discord", which is what was actually
   asked for.

### 3.1 Declaring intent

`tests/mihomo-routing-expectations.yaml` holds domain → expected group. Changing a
route means changing the expectation in the same commit, so the PR diff shows the
intent and a reviewer sees it. The gate fails on any deviation from the file.

Chosen over a commit-message marker because this repo has already been bitten by
squash-merge dropping a `Regression-Test-Override:` trailer, and over inferring
intent from the diff because reordering rules changes the routing of domains that do
not appear in the diff at all.

Side benefit: this file becomes the only place where the intended routing is written
down. Right now that knowledge exists nowhere.

---

## 4. Rollout

**Trigger:** a PR touching the config runs the full gate without deploying, and posts
the routing diff as a comment. Merging to master re-runs the gate and then deploys.

**Deployment order:**

1. `GET` the live template — this both gives the current state for the diff and puts
   the previous base64 in hand for free.
2. `PATCH` the panel with the candidate.
3. Force-refresh **only the canary**, `wrt-zmit` (formerly `wrt-me`) — the user's own
   device standing in for a client's dead one, so a bad half hour costs least there.
4. Verify on the canary against live hardware and a real OpenClash.
5. If clean, force-refresh the remaining routers.

Routers pull on their own anyway (`wrt-sh` every 60 minutes, the rest via a 03:00
cron), so forcing is an accelerator, not the mechanism — which is what makes the
rollback below effective.

**Rollback on canary failure:** restore the previous base64, force-refresh the canary
back, alert Telegram, and leave the other routers untouched. Because the other
routers only pull on a schedule and were never forced, clients never see the bad
config at all.

**Phones are out of scope** — per the user, they cannot be pushed to and rely on
their own auto-update.

### 4.1 Canary verification does not block on `as-7oj`

The canary check can run immediately over SSH on the router: query its mihomo API for
the pool state and curl the expectation domains through the mixed port, reading the
rule match from the log. It gets richer once the router probe agent from `as-7oj`
phase 2 lands, but it does not have to wait for it.

---

## 5. Non-goals

- Pushing config to phones.
- Testing against the live template by making a candidate live first.
- Asserting HTTP reachability as a gate condition (see 2.1).
- Keeping the panel as the source of truth.

---

## 6. Risks

- **The reconciliation commit is the dangerous one.** It defines "correct" for
  everything afterwards. It must be a faithful capture of the live template, reviewed
  as such, and not an opportunity to also tidy things up.
- **Out-of-band panel edits** through the web UI will silently reintroduce drift.
  Worth a periodic drift check that compares live against the repo and alerts — cheap
  to add once the pipeline exists.
- **The harness needs real proxy credentials** (a rendered subscription) to assemble a
  candidate. It must be fetched at run time from a secret token and never written to
  an artifact or a log.
- **Auto-rollback is itself automation that can misfire.** It is deliberately narrow:
  restore one known-good base64, refresh one router, alert.
- **Anti-bot noise** on third-party domains — mitigated by asserting on rule matches
  rather than responses.

---

## 7. Open items

- Confirm the domain list for `tests/mihomo-routing-expectations.yaml`; it should
  overlap with the site list from `as-7oj` rather than diverge from it.
- Decide where the canonical config file lives in the tree (`config/mihomo/` is the
  natural home) and set the workflow path filter accordingly.
