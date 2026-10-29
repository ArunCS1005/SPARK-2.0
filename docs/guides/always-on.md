# Keeping the laptop on

A server is only useful while it runs. Three things usually stop a laptop: the lid, sleep, and
Docker not starting after a reboot.

```bash
sudo ./scripts/always-on.sh              # lid closed = keep running; Docker starts at boot
sudo ./scripts/always-on.sh --no-sleep   # also never suspend or hibernate
./scripts/always-on.sh --dry-run         # only show what it would change
sudo ./scripts/always-on.sh --undo       # back to normal
```

What it changes:

- Writes `/etc/systemd/logind.conf.d/50-spark-lid.conf` so closing the lid does nothing. Your
  distribution's own `logind.conf` is left alone. It takes effect after the next reboot.
- `systemctl enable docker containerd`, so Docker starts at boot. SPARK's containers then start
  by themselves (they have restart policies). FileBrowser may restart a few times until Cosmos is
  ready; that is normal and takes about 20 seconds.
- With `--no-sleep`: masks the sleep, suspend and hibernate targets.

Also worth doing:

- Keep the laptop plugged in, and if its settings allow, limit charging to about 80 % to spare
  the battery.
- Desktop power settings (GNOME, KDE) can still put the laptop to sleep when idle. Set
  "Automatic suspend" to off there, or use `--no-sleep`.
- Leave some air around a closed laptop; they get warm.
