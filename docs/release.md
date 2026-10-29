# Shipping SPARK — release plan (draft, 2026-10-29)

How SPARK gets from this repository to someone's laptop without them cloning or building anything,
and what installing and updating then look like. Nothing here is built yet; facts are from GitHub's
and Docker's docs and from checks noted inline.

## What is shipped

The first release is the core: Cosmos, FileBrowser, the tunnel and the `spark` CLI. The add-ons
(Jellyfin, Tailscale, Nextcloud) come in a later release (`docs/roadmap.md`).

| Piece | Where it comes from | Built by us? |
|---|---|---|
| Cosmos, FileBrowser, cloudflared | Their own images, pinned tags in `.env` (`COSMOS_VERSION`, …) | No, pulled from Docker Hub as today |
| `spark` CLI (used by `init` and `spark`) | `ghcr.io/aruncs1005/spark-cli:<version>` | **Yes**: from `tools/spark-cli`, target `cli` |
| Install bundle | GitHub Release `v<version>` asset `spark-<version>.tar.gz` | **Yes**: `compose.yaml`, `config/`, `scripts/`, `.env.example`, `docs/guides/`, `README.md` |
| Checksums | Release asset `SHA256SUMS` | **Yes** |
| Installer | Release asset `install.sh` (small: downloads, checks and unpacks the bundle, then runs `scripts/install.sh`) | **Yes** |

Platforms: `linux/amd64` and `linux/arm64`. Checked: the pinned Cosmos, FileBrowser and cloudflared
tags and the `python:3.13-slim` base all publish both (`docker manifest inspect`), so a Raspberry
Pi 4/5 or an ARM laptop can run SPARK. The arm64 build has not been run yet.

The repository is public (GitHub API answers 200 without a login).

## Versions

- Tags `vMAJOR.MINOR.PATCH` (start at `v0.1.0`). A tag is the only thing that makes a release.
- Image tags: `0.1.0` and `0.1`. No `latest`: SPARK always pins (development.md, "Pinned versions").
  Builds of `main` may go to an `edge` tag for testing.
- The bundle's `.env.example` pins `SPARK_VERSION=0.1.0` alongside the upstream versions, so one
  release = one tested set of images.

## The release workflow (`.github/workflows/release.yml`, to write)

Triggered by pushing a `v*` tag. Jobs:

1. **test**: the existing CI jobs (unit tests, compose config, shellcheck, smoke test).
2. **image** (after test). Permissions `contents: read`, `packages: write`, `attestations: write`,
   `id-token: write`, as in GitHub's "Publishing Docker images" guide:
   - log in to `ghcr.io` with `GITHUB_TOKEN` (`docker/login-action`);
   - tags from `docker/metadata-action` (semver `{{version}}`, `{{major}}.{{minor}}`), image name
     written in lowercase (`ghcr.io/aruncs1005/spark-cli`; registries require lowercase names);
   - QEMU + Buildx, `docker/build-push-action` with `platforms: linux/amd64,linux/arm64`,
     `target: cli`, label `org.opencontainers.image.source=https://github.com/ArunCS1005/SPARK-2.0`;
   - build provenance with `actions/attest` (the guide's current step), so anyone can check the
     image with `gh attestation verify oci://ghcr.io/aruncs1005/spark-cli:<version> --owner ArunCS1005`.
   - Third-party actions pinned to commit SHAs, as the guide does.
3. **bundle** (after image). Permission `contents: write`: pin `SPARK_VERSION` in `.env.example`,
   pack `spark-<version>.tar.gz`, write `SHA256SUMS`, and create the GitHub Release with these
   assets and `install.sh` (`gh release create`), with notes from the commits since the last tag.

Workflows inside the repository link the package to it automatically, and a linked package inherits
the repository's access permissions. A first publish is private by default, so **after the first
release the owner makes the package public once** (package settings → change visibility); public
images can then be pulled without a login.

## Changes in the repository

- `compose.yaml`: `init` and `spark` use `image: ghcr.io/aruncs1005/spark-cli:${SPARK_VERSION:-dev}`
  and keep `build:` for development. Compose pulls by default when a service has both; whether it
  falls back to building when the tag does not exist (`dev`) is not stated in Docker's docs, so
  development keeps running `docker compose build` explicitly (as `install.sh` already does).
  **To test.**
- `scripts/install.sh`: pull instead of build when `SPARK_VERSION` is a release.
- `scripts/update.sh` (new): back up, download the new bundle, keep `.env` (add new keys only),
  set the new versions, `docker compose pull && docker compose up -d`, `spark setup`,
  `spark doctor`. If anything fails: `spark restore` of the backup it just made.
- `LICENSE`: none yet. Needed before publishing anything for others to use (owner's choice). SPARK
  only references the upstream images; it does not redistribute them (Cosmos has its own
  Commons Clause licence).

## What installing looks like for a user

```bash
# 1. Download the installer of the latest release and look at it if you like
curl -fsSLO https://github.com/ArunCS1005/SPARK-2.0/releases/latest/download/install.sh
less install.sh

# 2. Run it
bash install.sh
```

`install.sh`:

1. checks Linux, Docker and Compose ≥ 2.24 (same checks as today);
2. downloads `spark-<version>.tar.gz` and `SHA256SUMS` from the same release and stops if the
   checksum does not match;
3. unpacks into `~/spark` (asks first; never overwrites an existing `.env`);
4. runs `scripts/install.sh` from there: the questions (data folder, how people reach SPARK, time
   zone), `docker compose pull`, Cosmos and FileBrowser without a tunnel, `spark setup` asking for
   the owner account, then everything else and `spark doctor`.

The user never needs git, Python or a build. Everyday commands stay `docker compose run --rm spark …`
in `~/spark`.

Updating: `cd ~/spark && ./scripts/update.sh` (latest release) or `./scripts/update.sh v0.2.0`.
Uninstalling: `./scripts/uninstall.sh` as today.

## Before the first release (checklist)

- [ ] Owner picks a licence; add `LICENSE`.
- [ ] `release.yml` and the compose/install changes above, tested on a fork or with a `v0.0.x` tag.
- [ ] Smoke test of the pulled image on amd64; arm64 at least under QEMU.
- [ ] Owner makes the `spark-cli` package public after the first publish.
- [ ] CI (`ci.yml`) seen green on GitHub.
- [ ] Getting-started guide switched to the release installer.
