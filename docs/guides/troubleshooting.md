# Troubleshooting

Always start with the health check. It changes nothing and says what to do:

```bash
docker compose run --rm spark doctor
```

Logs: `docker compose logs cosmos` (or `filebrowser`, `tunnel`, `quick-tunnel`).

## "Cosmos safety settings are off"

Cosmos can delete Docker images and networks of *other* projects on the laptop and update itself
when these settings are off. Put them back right away:

```bash
docker compose up -d                      # init repairs the config file
docker compose run --rm spark setup       # and the running Cosmos
```

Do not turn "prune" or "auto update" on in the dashboard.

## Files: "Sign in with SPARK" fails or the page cannot be found

- Run `spark doctor`: it checks the `/files` route, the sign-in client and FileBrowser's config.
- Changed `SPARK_HOSTNAME`? Run `docker compose up -d`, then `spark setup`.
- Laptop-only mode (`spark.localhost`) needs `SPARK_HTTP_PORT=80`; `localhost:<other port>`
  cannot sign in, because Cosmos's sign-in links carry no port.
- Quick tunnel: its address changes whenever it restarts. Run `spark setup` again.

## The owner cannot create share links

Run `docker compose restart filebrowser` once after the owner's first sign-in to Files.

## FileBrowser keeps restarting

- Right after a reboot this is normal for about 20 seconds (it waits for Cosmos).
- Without a hostname FileBrowser cannot sign anyone in and is not started by the installer. Set
  `SPARK_HOSTNAME` (or `spark.localhost`) and run `spark setup`.
- `spark doctor` reports folder owners FileBrowser (uid 1000) cannot write to, with the fix.

## Lost admin token

SPARK talks to Cosmos with an API token made at first setup and kept in the `spark-state` volume.
If that volume was deleted, or the token "provisioning-token" was removed in the dashboard,
`spark setup` and `spark user` stop with "no admin token" or "rejects SPARK's admin token".

Fix it from a backup: `docker compose run --rm spark restore /data/backups/<file>.tar.gz`.

Without a backup: in the dashboard, create a new API token for the owner with all permissions,
then save it where SPARK keeps it (replace `<token>`):

```bash
docker compose run --rm --entrypoint python spark -c \
  "from spark_cli.state import State; State('/state').set('cosmos.admin_token', '<token>')"
docker compose run --rm spark setup
```

## Accounts: "free tier allows 5 accounts"

Pending invites count. `spark user list` shows them; remove ones that were never used.

## Port already in use

Another program uses `SPARK_HTTP_PORT` (8080 by default, 80 in laptop-only mode). Change the port in
`.env` (not for `spark.localhost`, which needs 80) or stop the other program.

## Start over

```bash
docker compose run --rm spark backup     # just in case
./scripts/uninstall.sh --purge           # deletes accounts and settings, keeps SPARK_DATA
./scripts/install.sh
```
