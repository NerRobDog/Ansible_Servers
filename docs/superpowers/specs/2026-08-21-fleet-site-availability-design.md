# Fleet site-availability monitoring (mini downdetector)

**Date:** 2026-08-21
**Epic:** `as-7oj`
**Russian version:** [2026-08-21-fleet-site-availability-design.ru.md](2026-08-21-fleet-site-availability-design.ru.md) — keep both in sync when editing
**Status:** design agreed, not yet implemented

---

## 1. Two problems, one session

### 1.1 Alert fatigue

The user asked to stop notifications arriving when nothing is wrong, so that the
ones that do arrive get read.

Investigation found the premise does not apply to our code. `monitor-remnawave-node`
gates its Telegram step on `notify_on_success`, which defaults to `false` for
scheduled runs, and the last twenty scheduled runs were all `success` — so not one
Telegram message was sent. `monitor-openwrt-fleet` has been `disabled_manually`
since 2026-04-29 and cannot be a source either. The only recurring Telegram traffic
during healthy operation is the nightly `collect-fleet-logs` archive, which the user
asked for deliberately.

The actual source is GitHub's own run notifications (email and GitHub Mobile). Those
are account settings, not repository state, and the user will change them:
`github.com/settings/notifications` → Actions → keep only *Only notify for failed
workflows*; plus Watch → Custom on the repo with Actions unchecked.

**Decision: no code work for goal 1.** The user was offered dedupe of repeated
failure alerts (one outage currently produces 48 identical messages per day) and
explicitly declined it. Recording that here so nobody "fixes" it later on the
assumption it was an oversight.

### 1.2 No visibility into what is actually reachable

Nothing in the fleet answers "is this site up, and if not, whose fault is it".
`blackbox_exporter` appears nowhere in `roles/`. The routers have a probe script at
`roles/openwrt_monitoring_agent/templates/openwrt-connectivity-probe.sh.j2`, but it
has never been deployed (`feature_openwrt_monitoring_agent: false` in the fleet
defaults) and it probes through a Passwall2 SOCKS port while this fleet runs
OpenClash (`feature_openwrt_passwall2: false`) — so its proxied path is wrong for
these devices even if it were enabled.

---

## 2. What we measure and why three layers

Four different faults look identical from the outside — "the site doesn't open".
The value of this system is telling them apart without logging into anything:

| Fault | Signature across the layers |
|---|---|
| The service itself is down | fails from **every** vantage point, direct and proxied alike |
| RKN/ISP blocked it | fails direct from the RU vantage, succeeds proxied and from foreign nodes |
| Our exit node died | mihomo reports the node dead; proxied path fails; direct still fine |
| A routing rule broke | mihomo says the node is healthy, but the rule-applied path fails |

### 2.1 Fleet nodes (6 hosts)

`blackbox_exporter` as a container in the `monitoring_agent` role. Chosen over
reusing the shell-script approach because it is the Prometheus standard and gives
response code, per-phase timings (DNS / connect / TLS / first byte), TLS certificate
expiry and body regex matching for free — and because targets live in
`prometheus.yml`, so **adding a site is one line with no Ansible run against any
host**.

Only the direct path is probed here. The nodes *are* the exit points; there is no
meaningful "through the proxy" path to test from them.

### 2.2 Routers (3 devices)

All three layers, per the user's decision:

1. **mihomo API.** Already exposed on every router at `127.0.0.1:<controller_port>`
   with the Bearer token in `fleet.openwrt.yml` (`openclash.controller_secret`).
   `GET /proxies` returns the whole exit pool with `alive` and `history[].delay`;
   `GET /group/<name>/delay?url=…&timeout=…` runs a live latency test against an
   arbitrary URL. This is the "which exit node is dead" signal, and it costs nothing
   to collect — the data already exists. Confirmed against the endpoints exercised by
   `mihomo-diag.sh`.
2. **curl through the mixed port** (7890, or 7892 on `wrt_me`). The only layer that
   proves routing *rules* actually deliver — this is what a TV on the LAN experiences.
3. **curl direct.** The only layer that reveals local ISP blocking.

Emitted as labelled metrics through the existing node-exporter textfile collector,
which the role already sets up.

### 2.3 Site list

Lives in a **git-tracked file in the repo**, not in the base64 fleet secret. Editing
a secret to add a domain is hostile; the whole point is that the user can append a
line. The list carries no credentials, so there is nothing to protect.

Proposed starting list, to be confirmed at implementation time:

- **Own infrastructure** — `ru.watchd0g.dev` (panel), `no.watchd0g.dev`
  (subscription distribution: if it dies every router silently stops getting config),
  and the per-node published domains derived from the fleet config.
- **Target services** — `youtube.com`, `chatgpt.com`, `claude.ai`, `x.com`,
  `instagram.com`, `discord.com`, `api.telegram.org`, `soundcloud.com` and
  `api-v2.soundcloud.com` (YUSIC streams from SoundCloud, so its reachability is
  operationally load-bearing, not incidental).
- **Controls** — `www.gstatic.com/generate_204` (clean 204, no anti-bot), `yandex.ru`
  (RU-side control that should always work from the RU vantage), `1.1.1.1` (raw
  connectivity below DNS).

---

## 3. Cadence

**Probe every 5 minutes, alert at `for: 10m`.**

This is a cost decision, not a taste one. Twelve sites across six nodes probed once a
minute is roughly 100 000 requests per site per day originating from third-party
VPS addresses. Cloudflare-fronted sites respond to that with 403s, and we would then
alert on outages caused by our own probe. At five minutes it is ~20 000/day, which
stays under the radar. Detection latency of 10–15 minutes is acceptable: services go
down for hours, not for seconds.

---

## 4. Alert delivery

**Alertmanager → Telegram directly**, via native `telegram_configs` (Alertmanager
v0.28 is already the pinned image). Real time, grouped, and it sends RESOLVED on its
own when the condition clears — none of which a 30-minute GitHub Actions poll can do.

A prerequisite that must not be missed: `roles/monitoring_stack/templates/alertmanager.yml.j2`
is currently nothing but

```yaml
route:
  receiver: default
receivers:
  - name: default
```

— a receiver with no integration. Every OpenWrt alert rule in
`openwrt-alerts.yml.j2` has therefore been firing into a black hole for months. This
must be fixed regardless of the site-availability work.

**Routing:** a dedicated Telegram forum topic (`message_thread_id`), separate from
the CI topic — live infrastructure alerts and CI run results are different things
with different urgency. `repeat_interval: 4h` while an alert stays firing, plus
RESOLVED.

The bot token has to reach the host. It goes through the fleet secret and is rendered
into the runtime config on `ae-us-1`, the same path the Grafana admin password
already takes.

### 4.1 What we alert on

| Condition | Meaning | Lands in |
|---|---|---|
| Site down from **every** vantage point | the service itself is down | phase 1 |
| Our proxied path fails while the site is alive elsewhere | our problem, not theirs | phase 2 (needs routers) |
| An exit node in the mihomo pool is dead | pool degraded | phase 2 (needs routers) |
| Deviation from the established baseline | a new block just appeared | phase 3 (needs history) |

Phase 1 therefore ships exactly one alert condition. That is intentional: it is the
one that needs no router access and no accumulated history, and it is enough to
prove the whole delivery path end to end before anything touches a customer device.

Direct unreachability from the RU vantage is *not* alerted — it is the normal,
permanent state for most target services and would drown the channel. It stays
visible on the dashboard.

### 4.2 Baseline deviation

Window **3 days**, threshold **90%**:

```promql
probe_success == 0
  and avg_over_time(probe_success[3d]) > 0.9
  and count_over_time(probe_success[3d]) > 500
```

The third clause is load-bearing, not defensive padding. Without it, in the first
hours after rollout `avg_over_time` is computed over a handful of samples, evaluates
to 1.0, and the first transient blip fires an alert.

A three-day window self-heals: a newly introduced block is caught the moment it
appears, and three days later it has become the new normal and the alert clears on
its own. The trade-off accepted is that a two-day provider outage can be absorbed
into "normal".

---

## 5. Dashboards and access

The user has never opened a Grafana dashboard here, so bespoke panels are the wrong
starting point. **Vendor the community dashboards** into the repo alongside the
existing `fleet.jsonnet`, verified live against the grafana.com API on 2026-08-21:

| ID | Dashboard | Downloads |
|---|---|---|
| 13659 | Blackbox Exporter (HTTP prober) | 51M |
| 1860 | Node Exporter Full | 142M |

Custom work is limited to a thin site × vantage × path matrix on top.

**Access: Tailscale Serve** — `https://<name>.tailc06517.ts.net` with real TLS, works
from the Mac and from the phone with no tunnel and no public exposure. Grafana today
binds `127.0.0.1` on `ae-us-1`, reachable only through an SSH tunnel, which is
unusable from a phone. The `ae-us-1` tailnet node is also renamed from the
auto-generated `bumpy-red-1` so the URL is memorable.

Telegram remains the primary channel. The dashboard is for "occasionally take a look".

---

## 6. CI reaching the routers

**The runner joins the tailnet** (`tailscale/github-action`, using the
`TAILSCALE_AUTH_KEY` secret refreshed on 2026-08-20).

Verified live on 2026-08-21 — all three routers are online in the tailnet:

```
100.100.172.55   wrt-me      online
100.65.182.54    wrt-nab     online
100.102.163.127  wrt-sh      online
100.118.204.14   bumpy-red-1 (ae-us-1, the stack)  online
```

This fixes a standing hole rather than merely serving this feature: `wrt_me` has
`proxy_jumps: []` and a tailnet address in its `zt_host` field, so CI cannot reach
it at all today — the fleet config says so in a comment. It also removes the
dependency on ZeroTier staying alive, which has already failed once on `wrt_me`.

Separately, `prometheus.yml.j2` targets OpenWrt hosts at `ansible_host`, which for
`wrt_sh` is a ZeroTier address unreachable from the US. Scrape targets must use the
tailnet address.

---

## 7. Phases

Phase 3 cannot be pulled forward: its rules need three days of accumulated history.

**Phase 1 — nodes.** `blackbox_exporter` on all six hosts; Prometheus job and site
list; Alertmanager → Telegram into the new topic; Grafana over Tailscale Serve;
dashboards 13659 and 1860 provisioned. Delivers a working downdetector without
touching a single customer router.

**Phase 2 — routers.** Runner into the tailnet; enable
`feature_openwrt_monitoring_agent`; rewrite the probe script for the three layers and
for OpenClash rather than Passwall2; fix the OpenWrt scrape address; re-enable
`monitor-openwrt-fleet`.

**Phase 3 — baseline rules**, once phase 1 has three days of data.

---

## 8. Non-goals

- Suppressing repeated failure alerts — offered and declined.
- Alerting on direct unreachability from the RU vantage — normal state.
- Exposing Grafana publicly.
- A hand-maintained per-pair expected-state table — the auto-baseline replaces it.

---

## 9. Risks

- **Anti-bot false positives.** Mitigated by the 5-minute cadence and by preferring
  `generate_204`-style endpoints. If a specific site still returns 403, treat it as
  a site-list problem, not an outage.
- **Bot token on disk on `ae-us-1`.** Same exposure the Grafana admin password
  already has; the host is not publicly exposed.
- **Router load.** Weak busybox devices; ~24 curl invocations per 5-minute cycle.
  Measure before and after on the first router, do not roll out blind.
- **Ephemeral tailnet nodes** from CI runs add churn, and the auth key expires —
  it already died once and silently broke a bootstrap behind `no_log: true`.
- **Phase 2 touches live customer routers.** Highest-risk part of the work, which is
  precisely why it is not phase 1.

---

## 10. Open items

- Telegram topic ID for the new alert topic — the user is creating it.
- Final site list confirmation.

---

## 11. Related side quests

Raised by the user mid-session, tracked separately so they do not blur this design:

- `as-sz9` — re-onboard the returning router `wrt-mkch` as `wrt-iv`. It is absent
  from the repo entirely, so this is a new host block, not a rename.
- `as-mci` — rename `wrt-me` to `wrt-zmit` and clear the surrounding rot: there are
  no `Host wrt-me` / `wrt-me-zt` blocks in `~/.ssh/config`, yet `wrt-nab-zt` and
  `wrt-sh-zt` both declare `ProxyJump` onto them; `wrt-nab` is configured with
  `HostName 192.168.5.115` while the fleet config gives its LAN as `192.168.2.1`; and
  comments on two hosts still claim `wrt-me` has been dead since 2026-08-12 when it
  is online.
