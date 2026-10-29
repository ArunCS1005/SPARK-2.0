# scripts/ — installers

Thin, host-specific wrappers. All real logic lives in `tools/spark-cli` (runs in a container,
so it behaves the same on Windows and Linux).

| Script | Does |
|---|---|
| `install.ps1` / `install.sh` | Check Docker, create `.env` and the `SPARK_DATA` folders, `docker compose up -d`, run `spark setup` |
| `uninstall.ps1` / `uninstall.sh` | Stop and remove containers; keeps your files unless asked |
| `power-settings.ps1` / `power-settings.sh` | Keep the laptop serving with the lid closed |
