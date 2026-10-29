# Developing SPARK

Conventions and interfaces every part of the repo relies on. If you change one of these, update
this file in the same change.

## Compose project

- Project name: `spark` (`name: spark` in `compose.yaml`). Containers are found by the labels
  `com.docker.compose.project=spark` and `com.docker.compose.service=<service>`.
- One network: the project's default network (`spark_default`; its name is never hardcoded, so a
  second project such as `spark-e2e` gets its own). Code finds it from the `cosmos` container's
  inspect data. The `cosmos` service carries the network alias `${SPARK_HOSTNAME}` so other
  containers reach Cosmos's OpenID issuer (`http://${SPARK_HOSTNAME}`) directly, never through the
  internet.

| Service | Profile | Image | Notes |
|---|---|---|---|
| `init` | — | `spark-cli:local`, built from `tools/spark-cli` | One-shot `spark init`, runs before Cosmos on every `up`. No Docker socket. `restart: "no"`. |
| `cosmos` | — | `azukaar/cosmos-server:${COSMOS_VERSION}` | `depends_on: init (service_completed_successfully)`. Publishes `${SPARK_HTTP_BIND}:${SPARK_HTTP_PORT}:80` only. `restart: unless-stopped` (setup makes Cosmos exit and come back). Config volume at `/config`. |
| `filebrowser` | — | `gtstef/filebrowser:${FILEBROWSER_VERSION}` | No published ports. Served by Cosmos at `/files`. Waits for Cosmos (its OpenID discovery must answer at start). |
| `tunnel` | `tunnel` | `cloudflare/cloudflared:${CLOUDFLARED_VERSION}` | `tunnel --no-autoupdate run`, token from `TUNNEL_TOKEN`. |
| `quick-tunnel` | `quick-tunnel` | `cloudflare/cloudflared:${CLOUDFLARED_VERSION}` | `tunnel --no-autoupdate --url http://cosmos:80`; random `*.trycloudflare.com` URL printed in its logs. Testing only. |
| `spark` | `cli` | `spark-cli:local` | Run on demand: `docker compose run --rm spark <command>`. |
| `jellyfin` | `media` | `jellyfin/jellyfin:${JELLYFIN_VERSION}` | Add-on, later release (`compose/media.yaml`). Never routed through the tunnel. |
| `tailscale` | `tailscale` | `tailscale/tailscale:${TAILSCALE_VERSION}` | Add-on, later release (`compose/tailscale.yaml`). |
| `nextcloud`, `nextcloud-db`, `nextcloud-redis` | `nextcloud` | `nextcloud:${NEXTCLOUD_VERSION}` … | Add-on, later release (`compose/nextcloud.yaml`). |

Named volumes: `cosmos-config`, `filebrowser-data`, `spark-state`; add-ons (later release) will
add `jellyfin-config`, `jellyfin-cache`, `tailscale-state`, `nextcloud-html`, `nextcloud-db`.
User files live only in the `${SPARK_DATA}` bind mount.

## Cosmos safety: the seed config

Cosmos holds the Docker socket, and by default it deletes things that are not SPARK's. 30 minutes
after any network-disconnect event on the machine it removes **every** network with no container
attached and **every** image not used by a container (`docker/network.go`, `NetworkCleanUp`).
It also replaces its own container when `AutoUpdate` is on. Only the config file turns this off
(`DockerConfig.SkipPruneNetwork`, `DockerConfig.SkipPruneImages`, `AutoUpdate`); there are no
environment variables for it.

So Cosmos never starts with a config SPARK did not write:

- `config/cosmos/seed.json` is the default config Cosmos itself writes for the pinned
  `COSMOS_VERSION`, with no keys or secrets in it. Regenerate it when bumping Cosmos.
- `spark init` (the `init` service) writes `/config/cosmos.config.json` from the seed before
  Cosmos's first start, with SPARK's values applied: `NewInstall: true`, `AutoUpdate: false`,
  both `SkipPrune*: true`, `HTTPConfig.HTTPSCertificateMode: "DISABLED"`, `HTTPConfig.Hostname`,
  `ServerCountry`.
- If the file already exists, `init` changes nothing except putting back those three safety keys
  when they are wrong, with a warning. `spark setup` enforces them through the API too, and
  `spark doctor` fails if they are off.
- If `init` fails, Cosmos does not start (fail closed).
- The `cosmos` service always sets `COSMOS_CONFIG_FOLDER=/config/`. Without it Cosmos picks its
  config folder by inspecting its own container through the Docker socket (`IsInsideContainer` in
  `docker/docker.go`). When that fails (no socket yet, a custom `hostname:`), it silently uses
  `/var/lib/cosmos/`, which is not a volume, and starts with a fresh default config, deletion
  enabled. Never set `hostname:` on the `cosmos` service.

## Nothing public before the admin exists

Until setup has run, Cosmos accepts the first-run setup from anyone who reaches it. Startup order:

1. `docker compose up -d cosmos filebrowser`: no tunnel yet (`init` runs as a dependency).
2. `docker compose run --rm spark setup`: creates the admin and wires everything.
3. `docker compose up -d`: starts the profiles in `COMPOSE_PROFILES`, including the tunnel.

The install scripts follow this order. Quick-tunnel mode is the one exception: setup needs the
random hostname first. The URL is unguessable and setup runs right after it, and this mode is for
testing only.

## The `spark` CLI container

Mounts (fixed paths the CLI code may rely on):

| Container path | Source | `spark` | `init` | Used for |
|---|---|---|---|---|
| `/var/run/docker.sock` | host Docker socket | ✓ | — | find/restart/stop containers, read quick-tunnel logs, inspect and connect the network |
| `/state` | `spark-state` volume | ✓ | — | `state.json` (admin API token etc.) |
| `/volumes/cosmos-config` | `cosmos-config` volume | ✓ | ✓ | seed config, backup/restore |
| `/volumes/filebrowser-data` | `filebrowser-data` volume | ✓ | ✓ | rendered `config.yaml`, backup/restore |
| `/data` | `${SPARK_DATA}` | ✓ | ✓ | `users/`, `shared/`, `media/`, `backups/` |
| `/app/config` | repo `config/` (read-only) | ✓ | ✓ | templates: `config/filebrowser/config.yaml`, `config/cosmos/seed.json` |

Environment: everything in `.env` (`env_file: .env`), plus at setup time only
`SPARK_ADMIN_USER`, `SPARK_ADMIN_PASSWORD`, `SPARK_ADMIN_EMAIL` passed with `docker compose run -e`.
Internal URL of Cosmos: `http://cosmos:80`; every request must carry `Host: ${SPARK_HOSTNAME}`
(Cosmos rejects unknown hostnames once set up).

FileBrowser reads its config from `FILEBROWSER_CONFIG=/home/filebrowser/data/config.yaml`, which is
`/volumes/filebrowser-data/config.yaml` for the CLI. It contains the OpenID client secret: mode
`0600`, owned by uid 1000 (FileBrowser's user). Writing it also gives the volume's folder to uid
1000, because FileBrowser creates its database there: `init` mounts the empty volume first, so
Docker makes it root's instead of copying in the image's folder.

### Commands

| Command | Module | Behaviour |
|---|---|---|
| `spark init` | `commands/init.py` | Run by the `init` service. Creates the `SPARK_DATA` folders, writes Cosmos's seed config (see above), and writes a placeholder FileBrowser config if none exists so FileBrowser never starts without one (it would create a default `admin` user). Idempotent; no network, no Docker socket. |
| `spark setup [--admin-user U] [--admin-email E] [--require-mfa]` | `commands/setup.py` | Idempotent. First run: Cosmos one-shot setup (`POST /cosmos/api/setup`, HTTPS mode `DISABLED`, `createAdminToken: true`), saves the admin token to state, waits for Cosmos to restart. Every run: reconciles Cosmos config (prune off, auto-update off, country, trusted proxies = Docker network subnet, local-IP HTTP access on), the `/files` route, the FileBrowser OpenID client (when it is created or replaced, restarts Cosmos: its sign-in server loads clients only at start), renders FileBrowser's config, restarts `filebrowser` when the config changed or it is not running, then gives the owner's Files account the share and API rights and the `SharedAdmin` source (creating the account first, login method `oidc`, if the owner has not signed in yet; see "FileBrowser admin channel"). Laptop-only mode: a `*.localhost` hostname is served over plain http and needs `SPARK_HTTP_PORT=80` (Cosmos's sign-in links carry no port); every other hostname uses https. Quick-tunnel mode: reads the current `trycloudflare.com` hostname from the `quick-tunnel` logs, uses it as the hostname and reconnects `cosmos` to the project network with that alias (Docker API). |
| `spark user add <name> [--admin] [--email E]` / `list` / `remove <name>` | `commands/user.py` | Cosmos accounts. `add` prints the invite (register) link. Surfaces the 5-user free-tier limit clearly. |
| `spark tunnel new <name> [--also <suffix>]… [--always-https]` / `list` / `remove <name>` | `commands/tunnel.py` | Cloudflare API (needs `CF_API_TOKEN`, `CF_ACCOUNT_ID`, `CF_ZONE_ID`). Creates (or reuses) remotely managed tunnel `spark-<name>`, ingress `<name>.<zone> → http://cosmos:80` (+ `<name>-<suffix>.<zone>` per `--also`) ending in `http_status:404`, proxied CNAMEs to `<id>.cfargotunnel.com`, prints the tunnel token. Refuses, before any change, a hostname that already has another DNS record. `remove` deletes its CNAMEs, its connections, then the tunnel. `--always-https` also needs Zone Settings: Edit. Used by the owner for friends, or by anyone for their own domain. |
| `spark doctor` | `commands/doctor.py` | Read-only health report; exit code 0 = healthy, 1 = problems found. |
| `spark backup [--output DIR]` / `spark restore <archive> [--yes]` | `commands/backup.py` | Stops the running `cosmos`/`filebrowser`, archives the `cosmos-config` and `filebrowser-data` volumes and `/state` (plus `manifest.json`, format 1) into `/data/backups/spark-<UTC timestamp>.tar.gz` (mode 0600, owned like `SPARK_DATA`), and starts them again even on failure. People's files in `SPARK_DATA` are not included. Restore refuses anything but plain files and folders under those three names (no links, absolute paths, `..`) before stopping anything, saves the current settings as `spark-before-restore-<ts>.tar.gz`, replaces the folders keeping owners and modes, and puts back the Cosmos safety keys (Cosmos is started without `init`). |

Exit codes: `0` success, `1` failure/unhealthy, `2` usage error. Errors are raised as
`spark_cli.errors.SparkError(message, hint=...)` and printed by `cli.py`; never print secrets
(tokens, passwords, client secrets) except the values a command exists to hand over
(`spark tunnel new` prints the tunnel token, `spark user add` prints the invite link).

`spark user` talks to Cosmos under the hostname setup saved, and refuses to run when
`SPARK_HOSTNAME` differs from it (run setup first).

### FileBrowser admin channel

FileBrowser gives an OpenID user neither the share right (until its next start) nor any source
that is not `defaultEnabled`, such as `SharedAdmin`. So `spark setup` acts as a FileBrowser admin
through the template's `auth.methods.jwt`: it signs a 5-minute HS256 token
(`sub: spark-cli`, `groups: [spark-automation]`, header `X-JWT-Assertion`) with
`filebrowser.automation_secret` from `/state/state.json` (rendered into the config as
`${FB_AUTOMATION_SECRET}`), and calls `http://filebrowser:80/files/api/users` inside the project
network. FileBrowser creates the admin user `spark-cli` on first use (no password; Cosmos names are
letters and digits only, so no person can have it). `init`'s placeholder config uses a random,
unsaved secret, so the channel never has a known key.

`POST /files/api/users` applies `userDefaults` to new users, permissions included
(`state.CreateUser` → `settings.ApplyUserDefaultsFrom`), so setup creates the owner with the three
sources, then sets `admin`, `share` and `api` with `PATCH`. An OpenID sign-in reuses an existing
account with login method `oidc` and changes neither its sources nor its rights
(`internal/web/auth.go` `getOrCreateAuthenticatedUser`).

### `spark doctor` checks

Read-only: no Docker, Cosmos or file changes. Cosmos settings come from its config file in the
volume, so the checks work while Cosmos is down. One line per check; a check that cannot run is a
FAIL and the rest still run.

| Check | FAIL when | WARN when |
|---|---|---|
| Containers `cosmos`, `filebrowser`, plus `tunnel` / `quick-tunnel` when their profile is on | missing, not running, unhealthy | image tag differs from the pinned `*_VERSION` |
| Cosmos safety keys (file) | `AutoUpdate`, `SkipPruneNetwork` or `SkipPruneImages` wrong; config missing; `NewInstall` still true | no last-good copy |
| Cosmos API with SPARK's token | token rejected, API error | no token saved |
| Hostname | Cosmos serves another name than `SPARK_HOSTNAME` (or the current quick-tunnel name); `*.localhost` without port 80 | no hostname (Files off) |
| `/files` route and OpenID client (file) | route missing or needs a Cosmos login; client missing or redirect outdated | |
| FileBrowser config | missing; placeholder; not for this hostname; data folder not owned by uid 1000 | readable by others |
| `SPARK_DATA` | `users`/`shared`/`backups` missing; uid 1000 cannot write `users` or `shared` | less than 5 GB or 5% free |

### Package layout (`tools/spark-cli`)

```
tools/spark-cli/
├── Dockerfile               targets: cli (default, ENTRYPOINT ["spark"]) and dev (tests)
├── pyproject.toml           deps: httpx, PyYAML · dev: pytest, respx, ruff
├── src/spark_cli/
│   ├── cli.py               argparse root; each command module exposes register(subparsers)
│   ├── settings.py          Settings.from_env() — the only place that reads os.environ
│   ├── state.py             State (JSON at /state/state.json)
│   ├── output.py            info/ok/warn/fail printing
│   ├── errors.py            SparkError
│   ├── docker_api.py        Docker Engine API over the unix socket (httpx)
│   ├── cosmos.py            Cosmos API client
│   ├── seed.py              Cosmos seed config (first-start safety)
│   ├── filebrowser.py       FileBrowser Quantum config rendering
│   ├── cloudflare.py        Cloudflare API client
│   └── commands/            init.py · setup.py · user.py · tunnel.py · doctor.py · backup.py
└── tests/                   pytest; HTTP mocked, no network, no Docker
```

Rules: every HTTP call goes through a client class that takes an `httpx.Client` (so tests inject a
mocked one); command modules contain orchestration only; no module except `settings.py` reads
environment variables.

## Running the CLI tests

Tests run in a throwaway container, so nothing is installed on the host:

```bash
# from the repository root
docker build --target dev -t spark-cli-dev tools/spark-cli   # again after changing dependencies
docker run --rm -v "$PWD:/repo" spark-cli-dev                # pytest + ruff
docker run --rm -v "$PWD:/repo" spark-cli-dev pytest -q tests/test_cosmos.py
docker run --rm -v "$PWD:/repo" spark-cli-dev ruff format src tests
```

The whole repository is mounted because tests read the templates in `config/`.

## Running Cosmos on a development machine

A development laptop usually has other Docker projects. Only ever start Cosmos through `init` (or
with a config file that has the three safety keys set), and use a separate project name and port for
experiments (`docker compose -p spark-dev`, `SPARK_HTTP_PORT=18080`). Never run `docker * prune`.

The CLI finds containers by the project name in `COMPOSE_PROJECT_NAME`, and `-p` does not pass it
into the container. A throwaway project therefore needs `COMPOSE_PROJECT_NAME=spark-dev` in the
environment of `init` and `spark`, for example an env file under `spike/` added to both services
with a small override file (`-f compose.yaml -f spike/dev/override.yaml`). Tear it down with
`docker compose -p spark-dev --profile cli down -v`.

## Pinned versions

Image tags live in `.env.example` and are bumped deliberately after testing an upgrade on a throwaway
install. Never use `latest`.

## Commits

Conventional, imperative subject lines (`Add tunnel command`, `Fix Cosmos hostname check`).
Commits carry only the human author — no co-author or tool attribution trailers.
