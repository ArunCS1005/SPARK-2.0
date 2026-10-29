# Try SPARK on your laptop

SPARK turns a Linux laptop into your own small cloud: a dashboard, accounts for you and up to four
friends, and **Files**, a web file manager with a private folder for each person, a shared folder,
and share links. This guide gets you a working SPARK with a public web address in about 15
minutes, so you can open it from your phone or send it to a friend.

You need: a Linux laptop (Ubuntu, Debian, Fedora, Mint, …), an internet connection, and about
2 GB of free disk space.

## 1. Install Docker (skip if you already have it)

SPARK runs inside Docker. On most Linux systems Docker's own install script is the quickest way:

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker "$USER"
```

Then **log out and log in again** (or restart the laptop), so you can use Docker without `sudo`.
Check that it works:

```bash
docker run --rm hello-world
docker compose version        # needs 2.24 or newer
```

Other ways to install Docker: <https://docs.docker.com/engine/install/>

## 2. Download SPARK

```bash
sudo apt install -y git       # if you do not have git (Fedora: sudo dnf install git)
git clone https://github.com/ArunCS1005/SPARK-2.0.git spark
cd spark
```

## 3. Run the installer

```bash
./scripts/install.sh
```

It asks a few questions. For trying SPARK out, press **Enter** to accept each suggestion:

| Question | What to answer |
|---|---|
| Folder for SPARK's files | Enter (uses `~/spark-data`) |
| How will people reach this SPARK? | **1**, a temporary public address |
| Time zone | Enter |
| Your country code | Enter, or your country (e.g. `US`) |

Then you choose the **owner account**, the admin of your SPARK:

- **name**: 3 to 32 letters or digits, no spaces (for example `arun`);
- **password**: at least 9 characters with an uppercase letter, a lowercase letter, a digit and a
  symbol, like `Sunny-Day-42` (make up your own). Typing is hidden. If it is not accepted, you
  are asked again.

The first run downloads about 500 MB and takes a few minutes. At the end you see a box like this:

```
================================================================================
  SPARK is ready

  Dashboard:  https://some-random-words.trycloudflare.com
  Files:      https://some-random-words.trycloudflare.com/files   (click "Sign in with SPARK")
  ...
================================================================================
```

That address works from anywhere: your phone on mobile data, a friend's laptop, anything.

## 4. Look around

- **Dashboard**: open the address and sign in with your owner account.
- **Files**: open the address followed by `/files` and click **Sign in with SPARK**.
  - **Home** is your private folder. Nobody else sees it.
  - **Shared** is visible to everyone with an account, read-only.
  - **SharedAdmin** is the same shared folder for you as the owner: add, change and delete files
    there, and everyone sees them in Shared.
  - Right-click a file → **Share** to make a link that works for anyone, without an account.
- Big files are fine: they are uploaded in small pieces.

## 5. Invite a friend

In the `spark` folder:

```bash
docker compose run --rm spark user add bob
```

It prints an invite link. Send it to Bob **privately**: whoever opens it first chooses the
password. Bob then signs in to Files and gets his own private Home. SPARK allows 5 accounts,
yours included.

## 6. Everyday use

| You want to | Run (in the `spark` folder) |
|---|---|
| See the address again, or get the new one after a restart | `./scripts/install.sh` |
| Check that everything is fine | `docker compose run --rm spark doctor` |
| Stop SPARK | `docker compose stop` |
| Start it again | `./scripts/install.sh` |
| Back up accounts and settings | `docker compose run --rm spark backup` |
| See everyone's accounts | `docker compose run --rm spark user list` |

**The temporary address changes** whenever the laptop or Docker restarts. Run
`./scripts/install.sh` again: it asks nothing the second time and shows the new address. Old links
stop working, so send the new address to your friends.

## 7. Good to know

- The address is public: anyone who learns it sees the sign-in page. Accounts and passwords
  protect everything else. Treat this mode as a test: no passport scans or passwords in files.
- During sign-in, one step can travel over plain `http`. Avoid signing in on open public Wi-Fi in
  this test mode. (With your own domain this can be closed; see [domains.md](domains.md).)
- SPARK only runs while the laptop is on and awake. To keep it running with the lid closed, see
  [always-on.md](always-on.md).

## 8. Something went wrong?

| Message or problem | What to do |
|---|---|
| "Cannot talk to Docker" | Log out and in again after step 1, or restart the laptop |
| "Port 8080 is already used" | Open `.env` in the `spark` folder, change `SPARK_HTTP_PORT=8080` to e.g. `8090`, run the installer again |
| The address shows an error or "530" | The address changed or SPARK stopped: run `./scripts/install.sh` |
| Anything else | Run `docker compose run --rm spark doctor`; send its output and the file `install.log` to whoever gave you SPARK |

More fixes: [troubleshooting.md](troubleshooting.md).

## 9. Remove SPARK

```bash
./scripts/uninstall.sh            # stops SPARK, keeps accounts and settings
./scripts/uninstall.sh --purge    # also deletes accounts and settings (type "delete" to confirm)
```

Your files stay in `~/spark-data` until you delete that folder yourself
(`sudo rm -rf ~/spark-data`, since some of its folders belong to SPARK's file manager).
