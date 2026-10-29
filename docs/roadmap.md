# Roadmap

| Release | Contents | State |
|---|---|---|
| **First release** (`v0.1.0`) | Cosmos + FileBrowser + Cloudflare Tunnel, the `spark` CLI (`init`, `setup`, `user`, `doctor`, `backup`/`restore`, `tunnel`), Linux installer, guides | Works and tested on Linux (see `HANDOFF.md`); release plan in [release.md](release.md) |
| Hardening | `spark tunnel harden` for own domains (Always Use HTTPS, HSTS, minimum TLS, a rule against forged visitor addresses) | Researched: [research/cloudflare-security.md](research/cloudflare-security.md) |
| Windows | WSL2 + Docker Engine | After Linux is stable (design.md, Decisions) |
| **Later release: add-ons** | Jellyfin (media), Tailscale (private remote access), Nextcloud (sync clients) | Not started |

## Add-ons: what is known so far

Research was cut short on 2026-10-29 when the add-ons moved to a later release. These notes are a
starting point only; re-check every version and fact before building.

- **Design rules that stay** (design.md): Jellyfin never goes through the Cloudflare tunnel (the free
  plan forbids serving mostly video); it is reached on the home network or over Tailscale. Nextcloud
  gets its own flat hostname `<name>-cloud.<domain>` (`spark tunnel new <name> --also cloud`), signs
  in through Cosmos, and uses 50 MB upload chunks because Cloudflare refuses requests over 100 MB.
- **Image tags on Docker Hub on 2026-10-29** (from the registry tag lists):
  - `tailscale/tailscale`: newest `v1.104.4` (2026-10-28).
  - `nextcloud`: newest `35.0.2-apache`. The `33-apache` pin that used to be in `.env.example` was out
    of date and has been removed.
  - `postgres`: newest `18.6-alpine`; check which major Nextcloud 35 supports.
  - `redis`: newest `8.10.3-alpine`; `valkey/valkey`: newest `9.1.3-alpine`.
  - `jellyfin/jellyfin`: tags look like `12.4.20261026-211555` (plus `12.4`, `12`); whether `12.4` is
    the server version or the packaging repository's version is **not verified yet**.
- **To find out first:** Tailscale's container settings (`TS_AUTHKEY`, `TS_STATE_DIR`,
  `TS_SERVE_CONFIG`, …), Jellyfin hardware acceleration in Docker, Nextcloud's `user_oidc` app and
  whether it accepts an `http://` discovery address on a private network, and whether Cosmos serves
  a route whose host differs from its own hostname.
