# compose/ — optional add-ons

Each file is a self-contained add-on included from the root `compose.yaml` and gated by a
[Compose profile](https://docs.docker.com/compose/how-tos/profiles/). Turn add-ons on with
`COMPOSE_PROFILES` in `.env`.

| File | Profile | Adds |
|---|---|---|
| `tunnel.yaml` | `tunnel` / `quick-tunnel` | cloudflared with your token, or a throwaway trycloudflare.com URL |
| `media.yaml` | `media` | Jellyfin (home network / Tailscale only — never through the Cloudflare tunnel) |
| `nextcloud.yaml` | `nextcloud` | Nextcloud + Postgres + Redis, login via Cosmos OpenID |
| `tailscale.yaml` | `tailscale` | Tailscale sidecar for private remote access to media |
