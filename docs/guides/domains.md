# Domains and tunnels

SPARK is reached through a **Cloudflare Tunnel**: the laptop opens an outgoing connection to
Cloudflare, and Cloudflare sends visitors of your address through it. No router settings, no open
ports, and it works behind college or hotel Wi-Fi. Cloudflare also provides the HTTPS certificate.

Each SPARK has **one address**; apps live on paths under it (`/files`).

## Three ways to get an address

| Way | Address | You need |
|---|---|---|
| Own domain | `spark.yourdomain.com` | A domain whose DNS is on Cloudflare (free plan is fine) |
| A friend's domain | `yourname.friend-domain.com` | A tunnel token from that friend |
| Testing | random `….trycloudflare.com` | Nothing (changes on every restart; public while it runs) |

## Getting a domain

Students can get a free domain for a year through the
[GitHub Student Developer Pack](https://education.github.com/pack) (for example Namecheap or
Name.com). Any registrar works. Then add the domain to a free Cloudflare account and change its
nameservers to Cloudflare's, as Cloudflare's setup screen shows.

## Own domain: make a tunnel with `spark tunnel`

1. In Cloudflare: **My Profile → API Tokens → Create Token → Custom token** with
   - Account → Cloudflare Tunnel → Edit
   - Zone → DNS → Edit (for your domain)
   - Zone → Zone Settings → Edit (only if you want `--always-https`)
2. Put the token, your account ID and the zone ID (both on the domain's overview page) in `.env`:
   `CF_API_TOKEN`, `CF_ACCOUNT_ID`, `CF_ZONE_ID`. Never share these.
3. Create the tunnel:

   ```bash
   docker compose run --rm spark tunnel new spark
   ```

   This creates the tunnel `spark-spark`, the address `spark.yourdomain.com`, and prints a
   **tunnel token**.
4. In `.env`: `SPARK_HOSTNAME=spark.yourdomain.com`, `TUNNEL_TOKEN=<token>`,
   `COMPOSE_PROFILES=tunnel`. Then run `./scripts/install.sh` (or, on an installed SPARK,
   `docker compose run --rm spark setup` and `docker compose up -d`).

`spark tunnel` never replaces an existing DNS record: if the name is taken, it stops before
changing anything.

## Giving a friend an address on your domain

```bash
docker compose run --rm spark tunnel new bob     # bob.yourdomain.com
```

Send Bob the printed token privately. Bob installs SPARK on his laptop and chooses "own domain or a
friend's token" with hostname `bob.yourdomain.com`. Bob never needs a Cloudflare account.

- `--also cloud` adds `bob-cloud.yourdomain.com` (for an app that needs its own address).
- `spark tunnel list` shows all SPARK tunnels and whether they are connected.
- `spark tunnel remove bob` deletes the tunnel and its DNS records; Bob's SPARK goes offline.

Good to know: your Cloudflare account can see that Bob's tunnel is connected and how much traffic
it carries (not the contents of HTTPS pages), and you can switch it off. Bob should be fine with
that. The free plan allows about 200 DNS records per domain.

## Video and Cloudflare's rules

Cloudflare's free plan does not allow serving mostly video through its network. Watching or
downloading your own video now and then in Files is personal use; streaming a media library is
not. That is why Jellyfin (media) is never put on the tunnel; use it at home or over Tailscale.
