# config/ — default app configuration

Templates the `spark setup` command copies into each app's Docker volume on first run.
Live configuration lives in named volumes, not here, so editing these files only affects new installs.

- `filebrowser/config.yaml` — FileBrowser Quantum: Home (private, read-write) and Shared (read-only) sources, OpenID login via Cosmos
- `cosmos/` — route and OpenID client definitions applied through the Cosmos API
- `nextcloud/` — Nextcloud settings for running behind Cosmos + Cloudflare (trusted proxies, 50 MB upload chunks)
