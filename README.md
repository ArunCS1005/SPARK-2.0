# SPARK — Self-hosted Personal Access Remote Kit

Turn a Linux laptop into a personal home server with one command:
private cloud storage, guest/admin accounts, a dashboard, and access from anywhere
through your own domain — no router port-forwarding needed (works behind college Wi-Fi).

> **Status:** works on Linux (tested end to end on a laptop-only install); Cloudflare paths and
> add-ons are next, Windows later. Progress: [`HANDOFF.md`](HANDOFF.md).
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

## Try it (Linux)

**New here? Follow [Try SPARK on your laptop](docs/guides/try-spark.md)**: install Docker, run one
command, choose an owner account, and get a public address you can open from your phone. About 15
minutes.

```bash
git clone https://github.com/ArunCS1005/SPARK-2.0.git spark
cd spark
./scripts/install.sh
```

The installer asks where to keep files and how people reach SPARK (a temporary public address for
testing, your own domain, or this laptop only at `http://spark.localhost`), then asks you to choose
the owner account and prints the address. Setting up your own domain:
[`docs/guides/getting-started.md`](docs/guides/getting-started.md).

## Everyday use

```bash
docker compose run --rm spark doctor              # health check, changes nothing
docker compose run --rm spark user add <name>     # invite someone (prints a private link)
docker compose run --rm spark backup              # save accounts and settings
docker compose run --rm spark tunnel new <name>   # owner: an address on your domain for a friend
```

## Guides

- [Try SPARK on your laptop](docs/guides/try-spark.md) (start here)
- [Getting started](docs/guides/getting-started.md)
- [Domains and tunnels](docs/guides/domains.md)
- [Accounts, folders and share links](docs/guides/access-control.md)
- [Keeping the laptop on](docs/guides/always-on.md)
- [Troubleshooting](docs/guides/troubleshooting.md)

For contributors: [`docs/development.md`](docs/development.md) (interfaces and how to run the tests)
and [`HANDOFF.md`](HANDOFF.md) (state of the work and next tasks).
