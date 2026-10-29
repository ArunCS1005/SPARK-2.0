# compose/ — optional add-ons

The core stack (Cosmos, FileBrowser, the Cloudflare tunnel and the `spark` CLI) is in the root
`compose.yaml`. Add-ons will live here, one file each, gated by a
[Compose profile](https://docs.docker.com/compose/how-tos/profiles/) that you turn on with
`COMPOSE_PROFILES` in `.env`.

| File | Profile | Adds | Status |
|---|---|---|---|
| `media.yaml` | `media` | Jellyfin, on the home network and Tailscale only, never through the tunnel | planned |
| `tailscale.yaml` | `tailscale` | Tailscale for private remote access | planned |
| `nextcloud.yaml` | `nextcloud` | Nextcloud + PostgreSQL + Redis, sign-in through Cosmos | planned |
