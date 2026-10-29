# Domains, Cloudflare and security — research, 2026-10-29

What SPARK relies on when it is reached through Cloudflare, what is weak, and what to change. Every
fact names its source: Cosmos v0.23.4 source (`cosmos:` paths under `src/`), Cloudflare's docs
(developers.cloudflare.com), or a live test. Items marked **UNVERIFIED** still need a live check.

## How a request travels

```
browser --HTTPS--> Cloudflare edge --tunnel--> cloudflared --HTTP--> Cosmos :80 --HTTP--> FileBrowser
```

TLS ends at Cloudflare. Cosmos runs in plain-HTTP mode (`HTTPSCertificateMode: DISABLED`), because
the tunnel already encrypts the hop from Cloudflare to the laptop.

## Own domain: what `spark tunnel new` sets up (verified in the API docs)

| Step | API call | Source |
|---|---|---|
| Remotely managed tunnel `spark-<name>` | `POST /accounts/{id}/cfd_tunnel` `{name, config_src: "cloudflare"}`; the reply carries `id` and `token` | "Create a tunnel (API)" guide |
| Ingress `<name>.<zone>` → `http://cosmos:80`, then `http_status:404` | `PUT /accounts/{id}/cfd_tunnel/{tid}/configurations` | same guide |
| Proxied CNAME `<name>.<zone>` → `<tid>.cfargotunnel.com` | `POST /zones/{zone}/dns_records` | same guide |
| Token again later | `GET /accounts/{id}/cfd_tunnel/{tid}/token` (result is the token string) | API reference |
| Remove | `DELETE …/cfd_tunnel/{tid}/connections`, then `DELETE …/cfd_tunnel/{tid}` (a tunnel with active connections cannot be deleted) | API reference |
| Always Use HTTPS (optional) | `PATCH /zones/{zone}/settings/always_use_https {"value": "on"}`; applies to every hostname of the zone; Free plan | "Always Use HTTPS" docs |

Free certificates cover one level below the zone, so `bob.example.com` works and
`files.bob.example.com` would not (design.md).

Status: tested against a fake API only. **UNVERIFIED** on a real account.

## Finding 1 — the sign-in step can travel over plain HTTP

- Cosmos builds its OpenID URLs with `http://` unless it serves TLS itself: `IsHTTPS` becomes true
  only when Cosmos starts its own HTTPS server (`cosmos:httpServer.go:121`), and the discovery
  document uses it (`cosmos:authorizationserver/oauth2_discover.go:47-61`). So FileBrowser sends the
  browser to `http://<host>/cosmos-ui/openid`. Seen live through the quick tunnel.
- In HTTP mode Cosmos sends no HSTS header (`cosmos:utils/middleware.go:150-156`), never marks its
  login cookie `Secure` (`cosmos:user/token.go:15-27`), and strips HSTS headers coming from backends
  (`cosmos:proxy/routeTo.go:248`).
- Cloudflare serves quick-tunnel hostnames over plain HTTP too (live: `http://…trycloudflare.com`
  answered 200, no redirect), and `trycloudflare.com` is not on the HSTS preload list
  (hstspreload.org status: unknown).

Effect: on that one redirect, a browser on shared Wi-Fi may send the Cosmos session cookie, or the
password if it has to sign in there, unencrypted to Cloudflare.

Fix for own domains (all Free plan, per Cloudflare's docs):

1. **Always Use HTTPS**: Cloudflare redirects every `http://` request to `https://`.
2. **HSTS** (setting id `security_header`; SSL/TLS → Edge Certificates): the header is sent on
   HTTPS responses; afterwards compliant browsers turn `http://` links into `https://` before
   sending anything, which closes the gap above. Options: max-age (1–12 months), includeSubDomains,
   preload, nosniff. Warning from the docs: while HSTS is on, the site must keep HTTPS, or it is
   unreachable for the max-age; turn it off by setting max-age to 0 and waiting out the old value.
   Start with a short max-age; do not use includeSubDomains or preload on a zone that also has
   plain-HTTP hosts.

The very first visit before any HSTS header is protected only by Always Use HTTPS (the request still
leaves the browser once over HTTP). SPARK always hands out `https://` links, so in practice the first
response that carries HSTS comes before the `http://` redirect.

Quick tunnels cannot get either fix: keep them for short tests, and say so (already in the guides).

A fuller fix would be Cosmos in HTTPS mode with a self-signed certificate behind cloudflared
(`noTLSVerify`), so Cosmos itself writes `https://` and sets `Secure` cookies. Not recommended now:
FileBrowser would have to skip certificate checks for the issuer, the local port would need HTTPS,
and the Cosmos setup wizard with self-signed HTTPS has lost admin accounts before (issue #549).

## Finding 2 — the visitor address Cosmos uses can be forged

- Every request passes `ClientRealIP` (registered at `cosmos:httpServer.go:602`), which stores
  `GetClientIP` as the client's address; the ban check (`BlockBannedIPs`) and SmartShield read it
  (`cosmos:utils/middleware.go:51-70`). When the connection comes from a trusted proxy,
  `GetClientIP` takes the **first** entry of `X-Forwarded-For` (`cosmos:utils/utils.go:930-937`). It
  never reads `CF-Connecting-IP`.
- `spark setup` trusts the project network, which contains cloudflared (correct).
- Cloudflare **appends** to an `X-Forwarded-For` header the visitor already sent (Cloudflare's "HTTP
  headers" reference), so the first entry is whatever the visitor wrote.

Effect: a visitor can choose the address SmartShield sees, to dodge its limits or to get another
address banned. **UNVERIFIED** end to end: whether cloudflared passes the header on unchanged
(needs a short live test with the owner's OK).

Fix for own domains: a Transform Rule that removes the incoming `X-Forwarded-For`. Cloudflare's
docs say the value of `X-Forwarded-For` cannot be modified, but the header can be removed, and
Cloudflare then adds it back with the visitor's real address before the request reaches the origin.
**UNVERIFIED** live; plan limits for Transform Rules not checked yet.

Related: FileBrowser logs the right-most `X-Forwarded-For` entry, which is cloudflared's address
(live), so its logs do not show visitors. Harmless: FileBrowser's sign-in is done by Cosmos.

## Other points

- **Tunnel tokens are passwords.** Whoever has a token can run that tunnel and serve that
  hostname. If one leaks: `spark tunnel remove <name>` then `spark tunnel new <name>` (new token).
- **The domain owner can see and change friends' traffic.** TLS ends at Cloudflare, and the zone
  owner controls the zone's rules and Workers, so a friend on `<name>.<owner-domain>` must trust the
  owner. Friends who do not should use their own domain. (Stated more softly in the domains guide
  today; make it explicit.)
- **The Cosmos dashboard is public** on the hostname and controls the Docker socket. Owner MFA is
  essential (`spark setup --require-mfa` makes it mandatory for all accounts). A Cloudflare Access
  policy in front of `/cosmos-ui` is possible later but would need exceptions for the OpenID and
  share paths. **UNVERIFIED**, not planned yet.
- Quick tunnels: random public address, 200 in-flight requests, no Server-Sent Events (design.md).

## Recommended next steps

1. `spark tunnel harden` (or `spark tunnel new --harden`): on the owner's zone set Always Use HTTPS,
   HSTS with a modest max-age (e.g. 6 months, no includeSubDomains or preload), minimum TLS 1.2, and
   the Transform Rule removing incoming `X-Forwarded-For`. Each through the API, idempotent, shown
   before applying. Needs an API token with Zone Settings: Edit and Transform Rules: Edit.
2. A short live test with the owner's OK: forged `X-Forwarded-For` through the tunnel with and
   without the rule; `http://` sign-in redirect with HSTS on.
3. `spark doctor`, when Cloudflare settings are present: warn if Always Use HTTPS or HSTS is off.
4. Guides: one paragraph each on HSTS (and how to turn it off safely), token leaks, and trusting
   the domain owner.
