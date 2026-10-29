# SPARK: handoff for continuing development on Linux

Read this first, then `docs/development.md` (the contract), `docs/design.md` (decisions and why) and
`docs/architecture.md` (how it fits together). This file says where the work stands and how to
continue it.

## Project in one paragraph

SPARK (Self-hosted Personal Access Remote Kit) turns a Linux laptop into a home server for its owner
(a student behind college Wi-Fi, no port forwarding) and up to four friends: **Cosmos Cloud**
(dashboard, accounts, OpenID provider, reverse proxy), **FileBrowser Quantum** at `/files` (signs in
with OpenID against Cosmos; private home folders, read-only shared folder, admin-made share links)
and **Cloudflare Tunnel** (own domain, a friend's subdomain of the owner's domain, or a random quick
tunnel for testing). A Python CLI, `spark`, runs in a container and automates setup and admin.
Jellyfin, Tailscale and Nextcloud are later add-ons.

## Rules for whoever continues (owner's instructions, non-negotiable)

1. **No AI or tool attribution anywhere**: no `Co-Authored-By` trailers, no "Generated with ...",
   no assistant or tool named as author or collaborator in commits, PRs, code, comments or docs.
   Commits carry only the human author. Do not name files or folders after an AI tool.
2. **Commit or push only when the owner asks.** The repository has no commits yet (`git init` only).
3. **Keep all work inside the project folder.** Scratch work goes in `spike/` (git-ignored).
4. **Never touch Docker resources that are not SPARK's**: no `docker system|image|network|volume
   prune`, never stop or remove other projects' containers. Use a separate compose project name and
   port for experiments (`docker compose -p spark-dev`, `SPARK_HTTP_PORT=18080`).
5. **Never start Cosmos without SPARK's seeded config** (see "Cosmos hazards" below). Always start it
   through `compose.yaml`, which runs `spark init` first.
6. Python runs in Docker, not on the host (see "Tests").
7. Verify facts about Cosmos, FileBrowser, Cloudflare and Docker in their source or official docs
   before coding against them; cite the file in a comment when the behaviour is surprising.
8. Write code like the existing code: small modules, type hints, comments only where they add
   information, `SparkError(message, hint=...)` for anything the user can act on, never print
   secrets.

## Linux machine setup

- Docker Engine with the compose plugin, **compose v2.24 or newer** (`compose.yaml` uses
  `env_file: required: false`). Rootful Docker; the user in the `docker` group.
- `git`, `curl`. No Python packages are installed on the host.
- No other host tools are needed: shellcheck and Mermaid rendering run in pinned images
  (`koalaman/shellcheck:v0.10.0`, `minlag/mermaid-cli:11.4.2`).

First steps after cloning:

```bash
cd SPARK
docker build --target dev -t spark-cli-dev tools/spark-cli
docker run --rm -v "$PWD:/repo" spark-cli-dev        # expect: 177 passed, ruff clean
tests/smoke/run.sh                                   # optional, ~2 min: whole stack end to end
```

Repository: `https://github.com/ArunCS1005/SPARK-2.0` (branch `main`). Commit locally after each
finished task; only the owner pushes.

## Tests

```bash
docker run --rm -v "$PWD:/repo" spark-cli-dev                             # pytest + ruff check
docker run --rm -v "$PWD:/repo" spark-cli-dev pytest -q tests/test_setup.py
docker run --rm -v "$PWD:/repo" spark-cli-dev ruff format src tests       # formatter
```

The dev image installs the package in editable mode at `/repo/tools/spark-cli`, so the mounted
working copy is what runs. Rebuild the image only when `pyproject.toml` dependencies change. Unit
tests never touch the network or Docker: HTTP is faked with `httpx.MockTransport`, Docker and Cosmos
with small fake objects.

## Running the stack locally

For users: `./scripts/install.sh` (see `docs/guides/getting-started.md`). For development, use a
throwaway project so a real install is never touched (details in `docs/development.md`, "Running
Cosmos on a development machine"): project `spark-dev`, an env file and a small override under
`spike/dev/`, laptop-only hostname `spark.localhost` on port 80, torn down with
`docker compose -p spark-dev --profile '*' down -v`.

Hostname modes: own/friend domain (profile `tunnel`), `spark.localhost` (laptop only, plain http,
port 80), quick tunnel (random public `trycloudflare.com` address, setup reads it from the logs), or
no hostname (Cosmos only; the installer then does not start FileBrowser).

## State of the work (2026-10-29)

Done and tested:

| Piece | Files | Notes |
|---|---|---|
| CLI skeleton | `cli.py`, `settings.py`, `state.py`, `output.py`, `errors.py`, `docker_api.py` | `settings.py` is the only module that reads the environment |
| Cosmos seed + `spark init` | `seed.py`, `commands/init.py`, `config/cosmos/seed.json` | Seeds Cosmos's config before its first start, repairs the safety keys, keeps a last-good copy and restores it when the config is missing or corrupt, fails closed if a Cosmos install exists without a config. Ran live. |
| Cosmos API client | `cosmos.py` | Paths and payloads verified in Cosmos v0.23.4 source; read calls confirmed against a live Cosmos |
| FileBrowser config rendering | `filebrowser.py`, `config/filebrowser/config.yaml` | Strict placeholder rendering; config written 0600, uid 1000 |
| Core compose | `compose.yaml` | `init`, `cosmos`, `filebrowser`, `spark` (profile `cli`), `tunnel`, `quick-tunnel`. Ran live (init + Cosmos healthy) |
| `spark setup` | `commands/setup.py` | Live on Linux: first run and zero-change re-run in local-only mode, with a hostname, and laptop-only (`spark.localhost`); hostname change; live network-alias change; restarts Cosmos after registering the OpenID client (Cosmos loads clients only at start). Quick tunnel only with fakes. |
| `spark user` | `commands/user.py` | Live: add → invite → register → Files sign-in, list (MFA from Cosmos's `auth.db`, columns verified in `utils/store_schema.go`), remove with owner/last-admin guards |
| `spark doctor` | `commands/doctor.py` | Live: all checks pass on a healthy install; FAILs (exit 1) when a safety key is off (tested on a copy of the config). Checks table in `docs/development.md` |
| `spark backup` / `restore` | `commands/backup.py` | Live round trip; hostile archives refused before anything is stopped; owners kept; safety keys put back; archive owned like `SPARK_DATA` |
| `spark tunnel` | `cloudflare.py`, `commands/tunnel.py` | Endpoints checked in Cloudflare's API docs; tested with a fake API only, **never run against a real account yet** |
| Scripts | `scripts/install.sh`, `uninstall.sh`, `always-on.sh` | install / re-run / uninstall / purge live on a throwaway copy; `always-on.sh` only with `--dry-run` |
| Smoke test + CI | `tests/smoke/`, `.github/workflows/ci.yml` | `tests/smoke/run.sh` passes locally (~2 min, project `spark-e2e`, no published port); CI not yet seen running on GitHub |
| Docs | `docs/guides/*`, `docs/architecture.md` | 5 guides; 9 Mermaid diagrams, each rendered once |

Verified results, including the Files permission checks, are in `docs/design.md`, "Spike results".

Open issues:

- The owner gets FileBrowser's share right only at the next FileBrowser start after their first
  sign-in (`auth.adminUsername` is applied at start). Documented workaround: restart `filebrowser`
  once. A proper fix is being researched.
- Not yet tested through Cloudflare (needs the owner's OK, it is public): the quick tunnel, the
  100 MB request limit, HTTPS cookies, real client IPs in SmartShield, `spark tunnel` against a real
  account.
- If the network drops right as first-run setup answers, Cosmos is set up but SPARK never saved the
  admin token; recovery is in `docs/guides/troubleshooting.md`, "Lost admin token".

## Next tasks, in order

Tasks 1–9 of the original plan are done (see the table). Finish each one with tests, commit it
locally, and let the owner check it before starting the next.

1. **Add-ons** in `compose/`: Jellyfin (LAN and Tailscale only, never the tunnel), Tailscale,
   Nextcloud (Postgres + Redis, OpenID against Cosmos, own flat hostname), and `docs/roadmap.md`.
2. **Owner share right** in FileBrowser without the manual restart.
3. **Cloudflare checks** with the owner's OK: quick tunnel end to end, then `spark tunnel` on the
   owner's domain.
4. **Windows** via WSL2 + Docker Engine (design decision of 2026-10-29), after Linux is stable.

## Startup order (nothing public before the admin exists)

1. `docker compose up -d cosmos filebrowser` (no tunnel; `init` runs first as a dependency)
2. `docker compose run --rm -e SPARK_ADMIN_USER -e SPARK_ADMIN_PASSWORD -e SPARK_ADMIN_EMAIL spark setup`
3. `docker compose up -d` (starts the profiles in `COMPOSE_PROFILES`, including the tunnel)

Until step 2, anyone who reaches Cosmos can run its first-run setup.

## Cosmos hazards and how SPARK handles them

All verified in the Cosmos v0.23.4 source (`git clone --depth 1 --branch v0.23.4
https://github.com/azukaar/Cosmos-Server`, code under `src/`).

| Hazard | Source | SPARK's answer |
|---|---|---|
| 30 min after any network-disconnect event on the machine, removes **every** unattached network and **every** image no container uses, machine-wide | `docker/network.go` `NetworkCleanUp`, `docker/events.go` | Seed config sets `DockerConfig.SkipPruneNetwork/SkipPruneImages = true`; `init` repairs them on every start, setup enforces them via the API, doctor must fail if they are off |
| Replaces its own container when `AutoUpdate` is on | `docker/docker.go` | `AutoUpdate: false` (same enforcement) |
| Picks its config folder by inspecting its own container through the socket; on failure silently uses `/var/lib/cosmos/` (not a volume) and starts a fresh default config | `index.go`, `docker/docker.go` `IsInsideContainer` | `COSMOS_CONFIG_FOLDER=/config/` in `compose.yaml`; never set `hostname:` on the cosmos service |
| Rewrites its config file in place; a crash can corrupt it. A missing config means a new install, and first-run setup **deletes all accounts** | `utils/utils.go`, `newInstall.go` `DeleteAllUsersLocal` | `init` keeps `/config/.spark/cosmos.config.last-good.json`, restores it, and refuses to re-seed a folder that already holds an install |
| A port in the hostname makes it spawn a helper that recreates its container | `docker/checkPorts.go` | `seed.py` rejects hostnames with `:`, `/`, `,` or spaces |
| App-store apps store data in host `/cosmos-storage` | `DockerConfig.DefaultDataPath` | setup sets it to `$SPARK_DATA/apps` (needs an absolute `SPARK_DATA`) |
| Dashboard metrics want the whole host filesystem at `/mnt/host` | `metrics/system.go` | Only `/proc` and `/sys`, read-only, at `/mnt/host/proc|sys`. Disk-usage lines for other mounts log harmless errors |
| Its admin can stop or delete any container on the machine through the dashboard | Docker socket | Documented; owner should enable MFA on their account |
| Exports a compose description of all containers into its config volume | `docker/export.go` | Harmless; inside its own volume |

Regenerating `config/cosmos/seed.json` after bumping `COSMOS_VERSION`: run the new image with
`--network none -e COSMOS_CONFIG_FOLDER=/config/ -v <throwaway volume>:/config` (no socket), copy out
`/config/cosmos.config.json`, blank `HTTPConfig.CACert`, `CAPrivateKey`, `SelfTLSCert`,
`SelfTLSKey`, `AuthPrivateKey`, `AuthPublicKey`, `TLSCert`, `TLSKey`, set both `*ValidUntil` to
`0001-01-01T00:00:00Z`, and check `test_repo_seed_carries_every_key_spark_sets` still passes.

## Cosmos API facts the CLI relies on (v0.23.4)

- Base `http://cosmos:80/cosmos/api`, JSON envelope `{"status": "OK", "data": ...}`; errors
  `{"status": "error", "message", "code"}`. Admin auth: `Authorization: Bearer cosmos_...`.
- Send `Host: <configured hostname>`; other names get 400. `localhost` is accepted while
  `HTTPConfig.AllowHTTPLocalIPAccess` is on (setup turns it on).
- `GET /status` answers without a login only during a new install (401 afterwards): that is how
  `is_new_install()` works. It reports `LicenceNumber` (5 accounts on the free tier).
- `POST /setup` (new install only): `hostname`, `httpsCertificateMode: "DISABLED"`,
  `allowHTTPLocalIPAccess`, `nickname` (3–32 alphanumeric), `password` (9–128, upper, lower, digit,
  special), `createAdminToken: true` → `data.adminToken`. Cosmos exits about 1 s later; the restart
  policy brings it back.
- `GET /config` blanks `AuthPrivateKey`, `TLSKey`, `CAPrivateKey`, `SelfTLSKey`. `PUT /config` keeps
  the auth keys, TLS cert/key, API tokens and OpenID clients from its own copy, then soft-restarts
  the HTTP server. Change OpenID clients only through `/openid`.
- Routes: `GET|POST /routes`, `GET|PUT|DELETE /routes/{name}`; changes restart the HTTP server.
- OpenID clients: `POST /openid {id, redirect}` returns the plain `secret` once (stored as bcrypt);
  several redirect URIs are comma-separated. `DELETE /openid/{id}`.
- Users: `POST /users {nickname, email, role}` (role 1 user, 2 admin) → `registerKey`,
  `registerKeyExp` (7 days, single use); 409 `UC014` when the 5-account limit is reached. Invite link:
  `https://<host>/cosmos-ui/register?t=2&nickname=<n>&key=<registerKey>`. `DELETE /users/{nickname}`.
- Healthcheck: `GET /cosmos-ui/` returns 200 without a login (`/status` needs one after setup).
  The image has `curl`.

FileBrowser Quantum facts (OpenID callback, required `X-Forwarded-Proto: https` route header,
read-only `Shared` with an admin-only `SharedAdmin` source, share permissions, chunked uploads,
recovery) are in `docs/research/filebrowser-quantum.md`.

## Leftovers from the Windows machine

The Windows laptop still has Docker volumes `spark_cosmos-config`, `spark_spark-state`,
`spark_filebrowser-data` and images `spark-cli:local`, `spark-cli-dev`. They are not needed on Linux
(start fresh there). Remove them on Windows only when sure:
`docker volume rm spark_cosmos-config spark_spark-state spark_filebrowser-data`.
