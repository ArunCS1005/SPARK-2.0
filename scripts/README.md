# scripts/ — host-side helpers (Linux)

Thin wrappers; the real logic lives in `tools/spark-cli`, which runs in a container. All pass
shellcheck.

| Script | Does |
|---|---|
| `install.sh` | Checks Docker and Compose (2.24+), writes `.env` from your answers (or keeps the existing one; the suggested mode is a temporary public address), starts Cosmos and FileBrowser without a tunnel, runs `spark setup` (which asks for the owner account and asks again when a name or password is refused), starts the rest, runs `spark doctor`, and ends with a box showing the address. Docker's own output goes to `install.log`. Safe to run again; that is also how a temporary address is refreshed after a restart. |
| `uninstall.sh` | Removes SPARK's containers and keeps accounts and settings. `--purge` also deletes this project's volumes and the `spark-cli:local` image after you type `delete`. Never touches `SPARK_DATA`. |
| `always-on.sh` | Needs sudo. Lid closed = keep running (a logind drop-in), Docker starts at boot; `--no-sleep` also masks sleep and hibernate; `--undo` reverts; `--dry-run` only prints. |
