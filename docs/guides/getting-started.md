# Getting started

SPARK turns a Linux laptop into a small home server: your own cloud storage (Files), accounts
for you and up to four friends, a dashboard, and an address on the internet that works even
behind college Wi-Fi.

## What you need

- A Linux laptop (Ubuntu, Debian, Fedora, Arch… anything with systemd) that can stay on.
- Docker Engine with the Compose plugin, version 2.24 or newer
  ([install guide](https://docs.docker.com/engine/install/)). Add yourself to the `docker` group:
  `sudo usermod -aG docker "$USER"`, then log out and in again.
- For access from anywhere: a domain on Cloudflare, or a tunnel token from a friend who has one
  (see [domains.md](domains.md)). You can try SPARK without either.

## Install

```bash
git clone <this repository> SPARK
cd SPARK
./scripts/install.sh
```

The installer asks:

1. **Where to keep files** (default `~/spark-data`). Everyone's files live in normal folders there.
2. **How people reach SPARK:**
   1. **A temporary public address** (the suggestion): a random `trycloudflare.com` address.
      Anyone who learns it can reach your SPARK's sign-in page while it runs, so use it for
      testing. It changes when the laptop restarts; run the installer again for the new one.
      Step-by-step for this mode: [try-spark.md](try-spark.md).
   2. **Your own domain or a friend's token**: e.g. `spark.yourdomain.com`, reachable from anywhere.
   3. **This laptop only**: `http://spark.localhost`. It uses port 80.
3. **Your time zone and country.**

Then it starts everything and asks you to choose the **owner account** name and password. The
password needs 9+ characters with an uppercase letter, a lowercase letter, a digit and a symbol.
Nothing is reachable from the internet until the owner account exists.

Your answers are saved in `.env` in the SPARK folder. Running the installer again keeps them.

## First steps

- **Dashboard:** open your address (for example `https://spark.yourdomain.com`) and sign in with
  the owner account. Turn on two-factor login for it (your account menu): the dashboard can
  control every container on the laptop.
- **Files:** open `<your address>/files` and click **Sign in with SPARK**.
- **Shared folder and share links:** the owner account can create share links from the first
  sign-in, and can add, change and delete files in Shared through the **SharedAdmin** folder.
  Everyone else sees Shared read-only. See [access-control.md](access-control.md).
- **Invite friends:** `docker compose run --rm spark user add <name>` prints a private invite link.
  See [access-control.md](access-control.md).
- **Keep it running with the lid closed:** see [always-on.md](always-on.md).

## Everyday commands

Run these in the SPARK folder.

| Task | Command |
|---|---|
| Health check | `docker compose run --rm spark doctor` |
| Accounts | `docker compose run --rm spark user list` / `add <name>` / `remove <name>` |
| Back up settings and accounts | `docker compose run --rm spark backup` |
| Restore them | `docker compose run --rm spark restore /data/backups/<file>.tar.gz` |
| Stop SPARK | `docker compose down` |
| Start it again | `docker compose up -d` |
| Remove SPARK (keeps your files) | `./scripts/uninstall.sh` |

`spark backup` saves accounts, passwords and settings, not people's files. Copy the files folder
(`SPARK_DATA`) with your usual backup tool.

Something wrong? Start with `spark doctor`, then [troubleshooting.md](troubleshooting.md).
