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
- Optional for later tasks: `shellcheck` (or the `koalaman/shellcheck` image), Node 20+ for
  rendering Mermaid diagrams (`npx -y @mermaid-js/mermaid-cli@11`).

First steps after unzipping:

```bash
cd SPARK
docker build --target dev -t spark-cli-dev tools/spark-cli
docker run --rm -v "$PWD:/repo" spark-cli-dev        # expect: 107 passed, ruff clean
git status                                           # nothing committed yet
```

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

```bash
cp .env.example .env
# edit .env: SPARK_DATA=$PWD/data (absolute path), SPARK_HOSTNAME= (empty for local-only),
#            COMPOSE_PROFILES= (empty: no tunnel yet)
docker compose up -d cosmos            # runs init first, then Cosmos on http://localhost:8080
SPARK_ADMIN_PASSWORD='...' docker compose run --rm -e SPARK_ADMIN_PASSWORD spark setup --admin-user <name>
```

`spark setup` ran live on Linux (see "State"): local-only mode, a hostname without a tunnel, and
re-runs that changed nothing. The quick-tunnel path is tested with fakes only so far.

Without a public hostname, setup configures Cosmos only. FileBrowser's OpenID sign-in needs a real
hostname: either a domain with a tunnel (`TUNNEL_TOKEN`, profile `tunnel`) or the `quick-tunnel`
profile for testing (a random public `trycloudflare.com` URL; setup reads it from the logs).

## State of the work (2026-10-29)

Done and tested:

| Piece | Files | Notes |
|---|---|---|
| CLI skeleton | `cli.py`, `settings.py`, `state.py`, `output.py`, `errors.py`, `docker_api.py` | `settings.py` is the only module that reads the environment |
| Cosmos seed + `spark init` | `seed.py`, `commands/init.py`, `config/cosmos/seed.json` | Seeds Cosmos's config before its first start, repairs the safety keys, keeps a last-good copy and restores it when the config is missing or corrupt, fails closed if a Cosmos install exists without a config. Ran live. |
| Cosmos API client | `cosmos.py` | Paths and payloads verified in Cosmos v0.23.4 source; read calls confirmed against a live Cosmos |
| FileBrowser config rendering | `filebrowser.py`, `config/filebrowser/config.yaml` | Strict placeholder rendering; config written 0600, uid 1000 |
| Core compose | `compose.yaml` | `init`, `cosmos`, `filebrowser`, `spark` (profile `cli`), `tunnel`, `quick-tunnel`. Ran live (init + Cosmos healthy) |
| `spark setup` | `commands/setup.py`, `tests/test_setup.py`, `tests/test_docker_api.py` | Ran live on Linux 2026-10-29 (project `spark-dev`): first run and zero-change re-run in local-only mode and with a hostname (`spark-dev.test`, no tunnel); live network-alias change; FileBrowser healthy, `/files` served by Cosmos, full Files sign-in through Cosmos verified in laptop-only mode (`spark.localhost`, port 80): Home writable, Shared read-only. Quick-tunnel only with fakes. |

Open issue: in local-only mode (no `SPARK_HOSTNAME`) FileBrowser restarts in a loop, because the
placeholder config points its OpenID issuer at `http://localhost` (itself) and FileBrowser exits when
discovery fails. Harmless but noisy; needs a decision (see the task 1 report).
Use `SPARK_HOSTNAME=spark.localhost` with `SPARK_HTTP_PORT=80` for a working laptop-only install.

Open issue: the owner gets FileBrowser's share permission only at the next FileBrowser start after
their first sign-in (`auth.adminUsername` is applied at start). Look at it in task 2.

Written, not tested yet:

- `commands/user.py`: `add|list|remove`. **Needs `tests/test_user.py`.** Its `read_mfa` reads
  Cosmos's SQLite `auth.db` read-only; the table and column names (`users`, `mfa_key`,
  `was_2fa_verified`) are unverified, so check them in Cosmos's store code or drop the MFA column.

Not started: `commands/doctor.py`, `commands/backup.py` (backup + restore), `cloudflare.py` +
`commands/tunnel.py` (stubs raise "not implemented"), `compose/` add-ons, `scripts/` installer,
`docs/guides/`, `tests/smoke` end-to-end test, CI, updated architecture diagrams, `docs/roadmap.md`.

## Next tasks, in order

Finish each one with tests before starting the next.

1. **`spark setup` tests**, then a live run on a throwaway project (`-p spark-dev`, port 18080):
   first run, re-run (no changes), quick-tunnel profile. Fix what breaks.
2. **FileBrowser end to end** with the quick tunnel: guest signs in via Cosmos, gets `Home/<name>`,
   cannot write to `Shared`, admin creates a share link that works without a session, a 120 MB upload
   goes through in chunks. Record the results in `docs/design.md`, "Spike results".
3. **`spark user`** tests and a live invite: `user add` → open the invite link → set a password →
   sign in to Files.
4. **`spark doctor`**: read-only checks with OK/WARN/FAIL lines and exit codes (contract table in
   `docs/development.md`). It must FAIL when the Cosmos safety keys are off.
5. **`spark backup` / `spark restore`**: stop cosmos+filebrowser, tar the volumes and state with a
   manifest, always restart in `finally`, tar-slip-safe restore.
6. **`spark tunnel new|list|remove`** + `cloudflare.py` (Cloudflare API: create tunnel with
   `config_src: cloudflare`, PUT ingress ending in `http_status:404`, proxied CNAME to
   `<id>.cfargotunnel.com`, GET token; optional Always Use HTTPS zone setting).
7. **`scripts/install.sh`** (checks Docker/compose, writes `.env`, follows the startup order below),
   `scripts/uninstall.sh` (keeps volumes unless `--purge`), `scripts/always-on.sh` (logind
   `HandleLidSwitch=ignore`, `systemctl enable docker`).
8. **`tests/smoke`** end-to-end runner + **CI** (`.github/workflows/ci.yml`: unit tests in the dev
   image, `docker compose config` for profile combinations, shellcheck).
9. **Docs**: user guides (getting started, domains, access control, always-on, troubleshooting) and
   detailed Mermaid diagrams in `docs/architecture.md` (system context, containers/volumes/network,
   trust boundaries, sequences for install, OpenID sign-in, share link, chunked upload, friend
   onboarding, backup/restore). Render each diagram once to check it parses.
10. Add-ons in `compose/` (Jellyfin LAN/Tailscale only, Tailscale, Nextcloud) and `docs/roadmap.md`.

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
