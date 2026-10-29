# SPARK architecture

SPARK is a Docker Compose project. A few containers on a Linux laptop give a dashboard with
accounts, a file manager, and a public address through a Cloudflare Tunnel, with no router ports
opened. Everything is ordinary containers, named volumes and one data folder. Why things are this
way is in [design.md](design.md); the interfaces the code relies on are in
[development.md](development.md).

## System context

```mermaid
flowchart LR
    owner([Owner]) -->|browser, phone| cf
    friends([Friends with accounts]) -->|browser, phone| cf
    anyone([Anyone with a share link]) --> cf
    cf[Cloudflare edge<br/>HTTPS, 100 MB per request] <-->|outgoing tunnel| laptop
    subgraph laptop[Owner's laptop]
        spark[SPARK containers]
        data[(SPARK_DATA folder)]
        spark --- data
    end
    cfapi[Cloudflare API] -.->|spark tunnel new<br/>owner only| cf
    owner -.->|CF_API_TOKEN| cfapi
```

## Containers, volumes and network

```mermaid
flowchart TB
    subgraph net[Project network spark_default]
        init[init<br/>spark init, runs once per up]
        cosmos[cosmos<br/>dashboard, accounts, OpenID,<br/>reverse proxy, port 80<br/>alias SPARK_HOSTNAME]
        fb[filebrowser<br/>FileBrowser Quantum at /files<br/>uid 1000]
        tunnel[tunnel or quick-tunnel<br/>cloudflared]
        cli[spark<br/>CLI, run on demand]
    end
    host[127.0.0.1:SPARK_HTTP_PORT] --> cosmos
    tunnel -->|http://cosmos:80| cosmos
    cosmos -->|/files| fb
    fb -->|OpenID discovery and tokens<br/>http://SPARK_HOSTNAME| cosmos
    cli -->|API, Host: SPARK_HOSTNAME| cosmos
    sock[[Docker socket]] --- cosmos
    sock --- cli
    vcfg[(cosmos-config)] --- cosmos
    vfb[(filebrowser-data)] --- fb
    vstate[(spark-state)] --- cli
    init --- vcfg
    init --- vfb
    cli --- vcfg
    cli --- vfb
    fb --- users[(SPARK_DATA/users)]
    fb --- shared[(SPARK_DATA/shared)]
```

| Service | Image | Role |
|---|---|---|
| `init` | `spark-cli:local` (built here) | Writes Cosmos's safe config before Cosmos ever starts, makes the data folders, writes a placeholder FileBrowser config. No Docker socket. |
| `cosmos` | `azukaar/cosmos-server:0.23.4` | Dashboard, accounts and two-factor, OpenID provider, reverse proxy. HTTP only; TLS ends at Cloudflare. The only service with a published port. |
| `filebrowser` | `gtstef/filebrowser:2.0.2-stable` | Files at `/files`, signs in through Cosmos (OpenID). |
| `tunnel` / `quick-tunnel` | `cloudflare/cloudflared:2026.10.0` | Outgoing tunnel to Cloudflare (profile `tunnel` with a token, or `quick-tunnel` for a random test address). |
| `spark` | `spark-cli:local` | `setup`, `user`, `tunnel`, `doctor`, `backup`, `restore`. |

Settings, accounts and secrets live in named volumes (`cosmos-config`, `filebrowser-data`,
`spark-state`). People's files live only in `SPARK_DATA`:

```
SPARK_DATA/
├── users/<name>/     each account's Home in Files
├── shared/           Shared (read-only in Files)
├── media/            for Jellyfin (add-on)
├── apps/             data of apps installed from Cosmos's app store
└── backups/          spark backup archives
```

## Trust boundaries

```mermaid
flowchart LR
    subgraph internet[Internet: untrusted]
        visitor([Visitor])
    end
    subgraph cloudflare[Cloudflare]
        edge[Edge: TLS, request limits]
    end
    subgraph docker[Laptop, Docker]
        cfd[cloudflared] --> cos[Cosmos<br/>SmartShield, login, routes]
        cos --> fbx[FileBrowser<br/>own sign-in check]
    end
    subgraph root[Root-equivalent]
        sock[[Docker socket]]
    end
    visitor --> edge --> cfd
    cos -.holds.-> sock
    cli[spark CLI] -.holds.-> sock
```

- Only Cosmos publishes a port, on `127.0.0.1` by default. FileBrowser is reachable only
  through Cosmos.
- Cosmos trusts `X-Forwarded-For` only from the project network (cloudflared), so its brute-force
  protection sees real visitors.
- Cosmos holds the Docker socket, which is root-equivalent. Its pruning and self-update are
  turned off by SPARK's config and checked by `spark doctor`. The owner account should use
  two-factor login.
- Secrets: `.env` (git-ignored) holds the tunnel token and, on the owner's machine, the Cloudflare
  API token. The Cosmos admin token and FileBrowser's sign-in secret live in volumes, mode 0600.

## Hostnames

One address per install; apps live on paths. Cloudflare's free certificate covers one level below
the domain, so `files.bob.example.com` would fail; `bob.example.com/files` works.

| Mode | `SPARK_HOSTNAME` | Scheme | Notes |
|---|---|---|---|
| Own domain | `spark.yourdomain.com` | https (Cloudflare) | tunnel made with `spark tunnel new` |
| Friend's domain | `bob.friend-domain.com` | https | the friend runs `spark tunnel new bob` |
| Test | (empty; read from quick-tunnel logs) | https | random, public while it runs |
| Laptop only | `spark.localhost` | http | port 80; browsers send `*.localhost` to the laptop itself |

Inside Docker the `cosmos` container also answers to the hostname (network alias), so FileBrowser
reaches Cosmos's OpenID endpoints directly, never through the internet.

## Install

```mermaid
sequenceDiagram
    actor O as Owner
    participant I as install.sh
    participant N as init
    participant C as Cosmos
    participant F as FileBrowser
    participant S as spark setup
    participant T as cloudflared
    O->>I: ./scripts/install.sh
    I->>I: check Docker and Compose, write .env
    I->>N: docker compose up -d cosmos filebrowser
    N->>N: write safe Cosmos config (no pruning, no auto-update)
    N->>C: Cosmos starts (waiting for first-run setup)
    I->>S: docker compose run spark setup
    S->>O: owner name and password?
    S->>C: POST /api/setup (HTTP only, admin token)
    C-->>S: admin token, then Cosmos restarts
    S->>C: config, /files route, OpenID client for FileBrowser
    S->>C: restart (loads the new OpenID client)
    S->>F: write config, restart
    I->>T: docker compose up -d (tunnel starts last)
    I->>S: spark doctor
```

Nothing is public until the owner account exists: the tunnel starts after setup. (Quick-tunnel
mode starts its tunnel first, because setup needs the random address.)

## Signing in to Files

```mermaid
sequenceDiagram
    actor U as Browser
    participant C as Cosmos
    participant F as FileBrowser
    U->>F: /files, Sign in with SPARK
    F-->>U: redirect to Cosmos /cosmos-ui/openid (state cookie)
    U->>C: sign in to Cosmos if needed, approve
    C-->>U: redirect to /files/api/auth/oidc/callback?code=…
    U->>F: callback
    F->>C: exchange code for tokens (inside Docker)
    C-->>F: ID token: preferred_username, role
    F-->>U: session cookie, Home = users/<name>, admin if role is admin
```

FileBrowser builds the callback address from the headers Cosmos sends; the `/files` route adds
`X-Forwarded-Proto: https` because TLS ends at Cloudflare.

## Share links

```mermaid
sequenceDiagram
    actor A as Owner
    actor V as Visitor (no account)
    participant F as FileBrowser
    A->>F: share file (expiry, optional password)
    F-->>A: https://host/files/public/share/<hash>
    A->>V: sends the link
    V->>F: open link (the /files route needs no Cosmos login)
    F-->>V: share page and download
```

## Large uploads

```mermaid
sequenceDiagram
    actor U as Browser
    participant E as Cloudflare
    participant F as FileBrowser
    loop every 10 MB chunk
        U->>E: POST chunk (offset, total size, session id)
        E->>F: through the tunnel and Cosmos
        F-->>U: offset, complete?
    end
    F->>F: assemble the file in the user's Home
```

Cloudflare refuses requests over 100 MB, so FileBrowser's web page sends big files in 10 MB pieces.

## A friend gets an address on the owner's domain

```mermaid
sequenceDiagram
    actor O as Owner
    participant S as spark tunnel new bob
    participant CF as Cloudflare API
    actor B as Bob
    O->>S: run on the owner's laptop
    S->>CF: check bob.domain has no other DNS record
    S->>CF: create tunnel spark-bob (managed by Cloudflare)
    S->>CF: ingress bob.domain → http://cosmos:80, else 404
    S->>CF: proxied CNAME bob.domain → id.cfargotunnel.com
    S-->>O: tunnel token
    O->>B: token, privately
    B->>B: install.sh with SPARK_HOSTNAME=bob.domain and the token
```

## Backup and restore

```mermaid
sequenceDiagram
    actor O as Owner
    participant S as spark backup / restore
    participant D as Docker
    O->>S: spark backup
    S->>D: stop filebrowser, cosmos
    S->>S: tar cosmos-config, filebrowser-data, state + manifest (0600)
    S->>D: start cosmos, filebrowser (always, even after an error)
    O->>S: spark restore archive
    S->>S: check every entry (plain files and folders only, known names)
    S->>D: stop filebrowser, cosmos
    S->>S: save current settings, replace folders keeping owners,<br/>put back Cosmos safety settings
    S->>D: start cosmos, filebrowser
```

Backups hold settings, accounts and secrets, not people's files in `SPARK_DATA`.

## Known limits

- 5 accounts per install (Cosmos free tier); share links need no account.
- Uploads through Cloudflare are cut into chunks of at most 100 MB (FileBrowser uses 10 MB).
- Cloudflare's free plan forbids serving mostly video; media (Jellyfin) stays off the tunnel.
- Cosmos and FileBrowser Quantum are each maintained by one person; versions are pinned and tested
  on a throwaway install before upgrading.
