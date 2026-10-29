# SPARK design — 2026-10-27

How the system works is in [`architecture.md`](architecture.md). This document records
**why**: intent, decisions, research findings, the build plan, and risks.

## Intent

Arun built a home server on an old Linux laptop (CasaOS, Jellyfin, Cloudflare Tunnel, btrfs) reachable
on a domain from behind college Wi-Fi. It worked but was hand-built and tied to that machine. SPARK
packages the idea so Arun and friends can install it on any **Windows or Linux** laptop with one command
and get private cloud storage, guest/admin access, a dashboard, and domain access. Media streaming is
wanted but **not a priority now**.

- **Goals:** one-command install on Windows + Linux; public access without port-forwarding; per-user
  storage with admin/guest roles; share links; dashboard with an app store.
- **Non-goals:** GUI installer for non-technical users (README + one command); macOS; a custom UI;
  running any central SPARK service.
- **Anti-goals:** host-filesystem dependencies (btrfs); router port-forwarding; a default stack too
  heavy for an old laptop; breaking Cloudflare's terms.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Audience | Arun + friends; README + one command | Small trusted group, no polished installer needed |
| Dashboard | Cosmos Cloud | CasaOS is Linux-only and needs the host |
| Files | FileBrowser **Quantum** default; Nextcloud optional add-on | Original FileBrowser is archived; Nextcloud is heavy |
| Domains | Own domain **or** `<handle>.<owner-domain>` via owner-created tunnel; quick tunnel for testing | Friends need no domain or Cloudflare account |
| Guest rights | Media; private folder; read-only shared | — |
| Approach | Cosmos stack, validated by a spike first; fallback Caddy + Authelia + Homepage | Cosmos on Windows is unproven |
| Platform | **Linux first** (Docker Engine), development on Linux too; Windows later via WSL2 + Docker Engine | Decided 2026-10-29 after the Windows spike: Docker Desktop only runs while a user is signed in, needs drive-path translation for bind mounts, can't `chown` on Windows folders, and shows a VM's metrics. Everything SPARK does runs in containers, so the code stays portable |
| Hostnames | One hostname per install, apps on paths (`/files`) | Nested subdomains fail free TLS; login cookie stays on one host |
| Media exposure | Jellyfin on LAN + Tailscale only, never through the tunnel | Cloudflare terms forbid video on free plans |
| Tooling language | Python CLI in a container (`spark`), thin bash installer | Logic lives in one testable codebase; a PowerShell installer can come with Windows support |
| Config storage | Named volumes for app settings/DBs; `SPARK_DATA` bind mount for files only | Windows folders can't `chown`/`chmod` |
| Local port | Bound to `127.0.0.1` by default; LAN opt-in for trusted home networks; everyone uses the hostname | Plain HTTP on college Wi-Fi is unsafe; campus client isolation blocks LAN anyway |
| App-store apps | Private by default (laptop + Tailscale); curated path-friendly apps may go through the tunnel | Per-app subdomains fail free TLS |
| Accounts | 5 per install (Cosmos free tier) is enough; everyone else gets share links | — |
| Video in FileBrowser | Allowed through the tunnel for personal use (occasional playback/download); no bulk streaming | Accepted small Cloudflare-terms risk |
| Share links | Admin only; guests cannot publish files; links work without an account | — |
| Reverse proxy | Cosmos's built-in proxy only; no Nginx | It is Go's `httputil.ReverseProxy` with no read/write timeouts and immediate flushing (`httpServer.go`, `proxy/routeTo.go`); TLS ends at Cloudflare; only Cosmos publishes a port, on 127.0.0.1. Nginx would duplicate routing and hide client IPs. Fallback if Cosmos must go: Caddy |
| Cosmos safety | `spark init` seeds Cosmos's config before first start; `COSMOS_CONFIG_FOLDER=/config/`; last-good config copy | Cosmos otherwise prunes every unused image/network on the machine and can lose its config (see development.md) |
| Existing CasaOS server | Out of scope: fresh start, existing DNS/tunnel untouched, old laptop not used for testing | — |

## Approaches considered

1. **Cosmos-based (chosen).** Closest to CasaOS: app store, accounts, OpenID provider, reverse proxy in
   one container. Risks: single maintainer, free tier capped at 5 accounts, Windows not officially
   documented (works with HTTP-only mode and port mapping per issue #549).
2. **Building blocks (fallback).** Caddy (`caddy-docker-proxy`) + Authelia (file-based users and groups,
   ~30 MB) + Homepage dashboard. All confirmed viable on Docker Desktop. No app store, more YAML,
   but no user cap.
3. **Custom SPARK UI.** Dropped.

## Research findings that shaped the design

| Finding | Consequence | Source |
|---|---|---|
| Cloudflare free certs cover only one subdomain level | No `files.<handle>.<domain>`; paths instead | developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/ |
| Tunnels, ingress, DNS and tokens are all creatable by API (Tunnel:Edit + DNS:Edit) | `spark tunnel new` can be fully automatic | developers.cloudflare.com/cloudflare-one/…/create-remote-tunnel-api/ |
| 100 MB request body limit; 125 s timeout | Chunked uploads only; 50 MB Nextcloud chunks | developers.cloudflare.com/support/…/error-413/ |
| Video over tunnel public hostnames prohibited on free plans | Jellyfin LAN/Tailscale only | developers.cloudflare.com/fundamentals/reference/policies-compliances/delivering-videos-with-cloudflare/ |
| Quick tunnels: random URL, 200 in-flight requests, no SSE | Testing only | developers.cloudflare.com/…/trycloudflare/ |
| Original FileBrowser archived 2026-09-01, unfixed CVE-2025-52904 | Use FileBrowser Quantum | github.com/filebrowser/filebrowser |
| Quantum: per-source per-user permissions, OIDC, anonymous shares, 10 MB chunked uploads | Home RW + Shared RO natively | filebrowserquantum.com/en/docs/configuration/sources/ |
| Quantum v2.0.0 had breaking config/DB changes | Pin exact tag; test upgrades | github.com/gtsteffaniak/filebrowser/releases |
| Cosmos free tier: 5 users; no Constellation | 5 accounts per install; Tailscale for private access | cosmos-cloud.io/pricing/ |
| Cosmos is an OIDC provider; `x-cosmos-user` header; no route labels | OIDC for apps; routes created via Cosmos API | cosmos-cloud.io/docs/openid/, src/proxy/routerGen.go |
| Cosmos on Windows: self-signed HTTPS in wizard loses the admin account; HTTP-only works | Always HTTP-only behind tunnel | github.com/azukaar/Cosmos-Server/issues/549 |
| Cosmos behind tunnel without TrustedProxies bans every visitor | Set trusted proxy to cloudflared | issues #616, #624 |
| Cosmos auto-updates by default | Turn off; pin version | Cosmos config `AutoUpdate` |
| Windows bind mounts: no chown, slow, no inotify | Named volumes for config/DB; scheduled scans | docs.docker.com/desktop/features/wsl/best-practices/ |
| Docker Desktop only runs while a user is signed in | Documented limit on Windows | docs.docker.com/desktop/setup/install/windows-permission-requirements/ |
| Jellyfin: containers on Windows unsupported by upstream, QSV unavailable on WSL; SSO plugin archived | Software transcoding on Windows; own Jellyfin users | jellyfin.org/docs/general/installation/container |
| Free domains: GitHub Student Pack (Namecheap .me, Name.com, .TECH); Freenom dead; DuckDNS can't use tunnels | Owner gets a Student Pack domain | education.github.com/pack |

## Build plan

> Status 2026-10-29: phases 0–4 are done on Linux (Windows moved to later, see Decisions); the
> tested state and the next tasks are in `HANDOFF.md`. The plan below is kept as it was written.

Each phase ends with something working and verified. Phases 0–3 are the core product.

### Phase 0 — Spike *(throwaway, `spike/`, git-ignored)*
Prove the risky assumptions on this Windows laptop before writing real code:
1. Cosmos 0.23.4 with port mapping, `COSMOS_HTTPS_MODE=DISABLED`, forward-slash paths: setup wizard
   completes, admin survives a restart.
2. Quick tunnel → Cosmos, opened from a phone on mobile data; cloudflared set as trusted proxy;
   SmartShield doesn't lock out.
3. FileBrowser Quantum at path `/files` behind Cosmos: OIDC login via Cosmos works; guest gets
   Home RW + Shared RO; anonymous share link opens in a private window.
4. Cosmos login cookie scope (host-only vs parent domain) — confirms the one-hostname design.
5. Idle RAM of the core stack.

**Exit:** all pass → Phase 1. Cosmos unusable → switch to the fallback approach and revise this doc.
Findings recorded under *Spike results* below.

### Phase 1 — Core stack
- `compose.yaml` (cosmos, filebrowser, `include:` of `compose/tunnel.yaml`), internal network,
  named volumes, healthchecks, pinned versions.
- `config/filebrowser/config.yaml` (sources, OIDC, `http.externalUrl`, share defaults).
- `scripts/install.ps1` and `install.sh`: check Docker, create `.env` from `.env.example` with prompts,
  create `SPARK_DATA` tree, `docker compose up -d`.
- `docs/guides/getting-started.md` with the manual Cosmos steps (until Phase 2 automates them).
- **Verify:** fresh install on Windows and on Linux (a WSL Ubuntu distro running its own Docker Engine,
  or a Linux VM — not the old CasaOS laptop);
  admin logs in through the tunnel; data survives `docker compose down && up`.

### Phase 2 — Automated setup and access control (`tools/spark-cli`)
- `spark setup`: Cosmos first-run via `POST /cosmos/api/setup`; routes; OIDC client for FileBrowser;
  trusted proxies; country; auto-update off; admin MFA required.
- `spark user add <name> [--admin]`: Cosmos account + invite link.
- Unit tests with mocked Cosmos API; `tests/` smoke test for the guest rules.
- **Verify:** guest sees only own home + read-only Shared; upload to Shared fails; guest can't create
  share links; admin share link works logged-out.

### Phase 3 — Domains
- `docs/guides/domains.md`: getting a Student Pack domain, moving DNS to Cloudflare, own-domain tunnel.
- `spark tunnel new <handle>` (planned as `spark friend new`): owner's API token → tunnel `spark-<handle>`, ingress
  `<handle>.<domain> → http://cosmos:80` + catch-all 404, proxied CNAME, prints token.
  `spark tunnel remove <handle>`.
- **Verify:** a second machine installs with the printed token and is reachable at `<handle>.<domain>`.

### Phase 4 — Operations
- `spark doctor`, `spark backup` / `restore` (named volumes → `SPARK_DATA/backups`), update procedure.
- `scripts/power-settings.*` and `docs/guides/always-on.md` (lid close, sleep, Docker auto-start).
- **Verify:** restore onto a fresh install brings back users, shares, settings.

### Phase 5 — Media *(profile `media`, `tailscale`)*
- Jellyfin with `SPARK_DATA/media`, LAN port 8096, scheduled library scans; Linux GPU override file.
- Tailscale sidecar for remote viewing; guest = Jellyfin user restricted to chosen libraries.
- **Verify:** stream on LAN and over Tailscale from mobile data; not reachable via the tunnel hostname.

### Phase 6 — Nextcloud *(profile `nextcloud`)* and SPARK market
- Nextcloud + Postgres + Redis, OIDC via Cosmos (official Cosmos guide), trusted proxies,
  `files.chunked_upload.max_size=52428800`, flat hostname `<handle>-cloud.<domain>`.
- `market/index.json` with SPARK-tuned app templates.

## Risks

| Risk | Mitigation |
|---|---|
| Cosmos misbehaves on Docker Desktop | Spike first; documented fallback stack |
| 5-account cap too low later | Arun confirmed 5 is enough; if that changes, the fallback stack has no cap |
| Video through the tunnel draws Cloudflare action on the owner's account | Personal use only; guides say so plainly; Jellyfin never on the tunnel |
| Quantum OIDC ↔ Cosmos untested combination | Spike item 3; fallback: header auth on a protected route + public share route |
| Single-maintainer upstreams, breaking releases | Pinned versions; upgrades tested in a throwaway install first |
| Laptop sleeps / Windows user signed out | Always-on guide; documented Docker Desktop limit |
| Owner's Cloudflare account sees friends' traffic metadata, can revoke them | Stated plainly in domains guide; own-domain mode available |

## Spike results

### 2026-10-29, Linux, laptop-only mode (no tunnel)

Cosmos 0.23.4 + FileBrowser Quantum 2.0.2-stable, installed with `spark init` + `spark setup` on a
throwaway compose project, hostname `spark.localhost` on `127.0.0.1:80`. Requests were scripted with
curl the way a browser makes them (Cosmos login, FileBrowser's OpenID start, Cosmos's consent form,
callback).

| Check | Result |
|---|---|
| Owner signs in to Files through Cosmos | Works; FileBrowser admin from the `role` claim; Home `/arundev` |
| Guest invited with `spark user add`, registers, signs in | Works; Home `users/guest1` created at first sign-in; not admin, no share right |
| Guest writes own Home | Works |
| Guest writes Shared | Refused: 403 `source is read-only` (owner too) |
| Guest reads another home or SharedAdmin | Refused (404 / 403) |
| Guest creates a share link | Refused (403) |
| Owner share link without a session | Share page and download both 200 |
| 120 MB upload in 10 MB chunks as guest, through Cosmos with SmartShield on | Works; file identical (sha256) |
| Cosmos restarted alone | Sign-in still works (its signing keys are kept in its config) |
| FileBrowser started while Cosmos is down (reboot order) | FileBrowser restarts until Cosmos answers, then healthy (about 20 s) |
| `docker compose down` + `up` | Accounts, files and sign-in kept; `spark setup` finds nothing to change |

Found and fixed on the way: Cosmos loads OpenID clients only when its web server starts, so setup
now restarts Cosmos after registering the FileBrowser client; FileBrowser could not create its
database in a volume first mounted by `init`; the `/files` route was rewritten on every setup run.

Since fixed: the owner had FileBrowser's share right only after FileBrowser's next start, and no
write access to Shared. `spark setup` now prepares the owner's Files account through FileBrowser's
JWT admin channel (see `docs/research/filebrowser-quantum.md`); verified live and by the smoke test.

### 2026-10-29, quick tunnel (tested by the owner)

Dashboard, Files sign-in, guest limits, share links from a phone on mobile data and a large upload
all worked through `https://<random>.trycloudflare.com`. Found: Cosmos sends the browser to its
sign-in page with `http://` (it only writes `https://` links when it serves TLS itself,
`httpServer.go:121`), and Cloudflare serves quick tunnels over plain HTTP too, so that step can
travel unencrypted. FileBrowser logs the tunnel's address instead of the visitor's; Cosmos's
SmartShield uses the real address (`utils.GetClientIP`, X-Forwarded-For from trusted proxies).

## Open questions

None. Builder-check questions were resolved on 2026-10-27 (see Decisions).
