# scripts/ — host-side helpers (Linux)

Thin wrappers; the real logic lives in `tools/spark-cli`, which runs in a container. All pass
shellcheck.

| Script | Does |
|---|---|
| `install.sh` | Checks Docker and Compose (2.24+), writes `.env` from your answers (or keeps the existing one), starts Cosmos and FileBrowser without a tunnel, runs `spark setup` (which asks for the owner account), then starts the rest and runs `spark doctor`. Safe to run again. |
| `uninstall.sh` | Removes SPARK's containers and keeps accounts and settings. `--purge` also deletes this project's volumes and the `spark-cli:local` image after you type `delete`. Never touches `SPARK_DATA`. |
| `always-on.sh` | Needs sudo. Lid closed = keep running (a logind drop-in), Docker starts at boot; `--no-sleep` also masks sleep and hibernate; `--undo` reverts; `--dry-run` only prints. |
