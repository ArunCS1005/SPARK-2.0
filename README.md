# SPARK — Self-hosted Personal Access Remote Kit

Turn a Linux laptop into a personal home server with one command:
private cloud storage, guest/admin accounts, a dashboard, and access from anywhere
through your own domain — no router port-forwarding needed (works behind college Wi-Fi).

> **Status:** in development (Linux first; Windows later). See [`HANDOFF.md`](HANDOFF.md).
> Design: [`docs/design.md`](docs/design.md) ·
> Architecture: [`docs/architecture.md`](docs/architecture.md)

## What's inside

| Piece | Job |
|---|---|
| **Cosmos Cloud** | Dashboard, login + accounts (admin/user), reverse proxy, app store |
| **Cloudflare Tunnel** | Puts the laptop on your domain from behind any NAT |
| **FileBrowser Quantum** | Drive-like web file manager: private home folders, read-only shared folder, share links |
| **Jellyfin** *(optional)* | Media streaming on your home network / Tailscale |
| **Nextcloud** *(optional)* | Full sync client experience (phone/desktop apps) |

## Repository layout

```
SPARK/
├── compose.yaml          Core stack: Cosmos + Cloudflare Tunnel + FileBrowser Quantum
├── compose/              Optional add-ons (media, nextcloud, tailscale), enabled via COMPOSE_PROFILES
├── config/               Default app configuration seeded on first setup
├── scripts/              One-command installer (bash)
├── tools/spark-cli/      `spark` helper: first-run setup, users, tunnels, health checks, backups
├── market/               SPARK's own Cosmos app-store source (app templates)
├── tests/                End-to-end smoke tests for a running install
├── docs/                 Architecture, guides, design spec
├── .env.example          Every setting SPARK reads, documented
└── README.md
```

## Quick start *(planned — not working yet)*

```powershell
# Windows (Docker Desktop installed and running)
git clone <repo> SPARK; cd SPARK; .\scripts\install.ps1
```

```bash
# Linux
git clone <repo> SPARK && cd SPARK && ./scripts/install.sh
```
