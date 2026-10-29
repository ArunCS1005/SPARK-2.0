# FileBrowser Quantum v2.0.2-stable: facts for SPARK

Researched 2026-10-27.

> The `spike/` paths below were a git-ignored scratch folder that has since been deleted. Fetch the
> sources again with `git clone --depth 1 --branch v2.0.2-stable
> https://github.com/gtsteffaniak/filebrowser` and `git clone --depth 1 --branch v0.23.4
> https://github.com/azukaar/Cosmos-Server`. The mock Cosmos harness was not kept.

- **Source:** `github.com/gtsteffaniak/filebrowser`, tag `v2.0.2-stable`, commit `2c071d1e6eb4`. This
  is the same commit as the `org.opencontainers.image.revision` label of the local
  `gtstef/filebrowser:2.0.2-stable` image (digest `sha256:de2b4c4d…`). A checkout is at
  `spike/upstream-filebrowser-src/v2.0.2-stable/`.
- **Citations:** paths are relative to `backend/` unless prefixed `frontend/` or `_docker/`.
  Paths prefixed `cosmos:` are in `spike/upstream-cosmos-src/v0.23.4/src/`.
- **Tags:**
  - **[run]** means I saw it by running the image against a mock Cosmos. The mock is
    `spike/fb-research/mock_cosmos.py`. It copies Cosmos's OIDC endpoints, claims and
    reverse-proxy header behaviour from the Cosmos source.
  - **[src]** means I read it in the source only.
  - **UNVERIFIED** means it was not confirmed.
- **Config under test:** `config/filebrowser/config.yaml`, mounted unchanged. FileBrowser expanded
  the placeholders from the container environment.

## Answers in one place

| Question | Answer |
|---|---|
| OIDC callback to register in Cosmos | `https://<SPARK_HOSTNAME>/files/api/auth/oidc/callback` (exact). This needs the Cosmos `/files` route to send `X-Forwarded-Proto: https` (see §3). [run] |
| Port / user | `:80`, uid/gid 1000 (`filebrowser`) [run] |
| Config file | env `FILEBROWSER_CONFIG` or flag `-c` (both used in testing). The image default is `/home/filebrowser/data/config.yaml` [run] |
| Database | env `FILEBROWSER_DATABASE_PATH`, image default `/home/filebrowser/data/database.sqlite`. Put the named volume on `/home/filebrowser/data` [run] |
| Healthcheck | `curl -fsS http://localhost:80/files/health` returns `{"message":"ok"}`. curl and busybox wget are in the image [run] |
| String `role: "admin"` with `groupsClaim: role`, `adminGroup: admin` | Works: a plain string is accepted (§3) [run] |
| Admin re-evaluated each login? | Promotion only. A user who loses the Cosmos admin role stays a FileBrowser admin [run] |
| Guest result | Home = `/srv/users/<name>` read-write, Shared read-only, cannot create shares [run] |
| Admin result | admin=true. Same Home/Shared rights as a guest, and no share permission until granted (§4) [run] |
| Password login | Can be disabled entirely (`password.enabled: false`). Recovery uses the CLI plus a recovery config (§7) [run] |
| Env expansion in config | Yes, `$VAR` and `${VAR}` in every string value. `FILEBROWSER_OIDC_CLIENT_SECRET` overrides the file [run] |

## 1. Container

- **Image facts** [run, `_docker/Dockerfile:29-35,47-50`]:
  - `USER filebrowser` (uid 1000), `WORKDIR /home/filebrowser`, `EXPOSE 80`.
  - `ENV FILEBROWSER_CONFIG=/home/filebrowser/data/config.yaml` and
    `FILEBROWSER_DATABASE_PATH=/home/filebrowser/data/database.sqlite`.
  - Default `http.port` is 80 (`pkg/settings/config.go:858`). Binding 80 as uid 1000 works under
    Docker's default sysctl.
- **Built-in `HEALTHCHECK` is meaningless under a base URL.** It runs
  `curl -f http://localhost:80/health`. With `baseURL: /files` that path returns `301 → /files/`,
  and `curl -f` counts that as success. Override it in compose with `/files/health`.
  - Also healthy: `/files/api/health` and `/files/public/api/health`.
  - Source: `internal/web/httpRouter.go:21-22,202,205-210`, `internal/web/http.go:504-510` [run].
- **Startup is fatal if the OIDC issuer is unreachable or its issuer string differs.** The process
  exits with code 1 and does not retry. Seen: `connection refused`, and
  `issuer URL provided to client ("http://h/") did not match ... ("http://h")`.
  - Source: `pkg/settings/config.go:543-548`, `pkg/settings/auth.go:207-210`, go-oidc v3.21.0 [run].
  - In compose: `depends_on: cosmos: condition: service_healthy` plus `restart: unless-stopped`.
- **The SPARK config must exist before the first start.**
  - The image copies its own `config.yaml` into a fresh data volume. That file enables password
    login with a `/srv` source.
  - On an empty database, password auth makes `QuickSetup` create a password user `admin` with a
    random password, logged once. Source: `internal/state/state.go:79-83`,
    `internal/state/quick_setup.go:20-25` [src].
  - That user would block a Cosmos user also called `admin` (login-method lock, §3).
  - If a bind-mounted config file does not exist yet, Docker creates a directory in its place [run].
- **Every `*-config.yaml` next to the config file is merged into it.** Mount config.yaml alone in
  its own directory. Source: `pkg/settings/yaml.go:28-33` [src].
- **Cache directory.** `server.cacheDir` defaults to `tmp`, i.e. `/home/filebrowser/tmp` in the
  container layer.
  - It holds the index SQLite (`tmp/sql`), thumbnails and the download spool. Losing it only
    triggers a re-index.
  - Startup writes a 10 MB speed-test file there.
  - Source: `pkg/settings/config.go:162-281,874` [run].
- **Backups.** The main database is SQLite in WAL mode (`database.sqlite-wal` and `-shm` were
  present) [run]. Stop the container before `spark backup` archives the volume.
- **Files created on SPARK_DATA** get mode 644 and directories 755, owned by uid 1000
  (`server.filesystem` defaults, `pkg/settings/config.go:883-886`). On Linux, `users/` and `shared/`
  must be writable by uid 1000 [src].

## 2. Base URL and URL settings

- **`http.baseURL: /files`** is normalised to `/files/` (`pkg/settings/config.go:507-513`). Every
  route moves under it [run; `internal/web/httpRouter.go:179-203`]:
  - API: `/files/api/…`
  - Public shares: `/files/public/…`
  - Static assets: `/files/public/static/…`. All asset URLs in index.html were checked.
  - `/files/health`
  - `/files/swagger/index.html` (admins only)
- **Redirects.** `/files` returns 307 to `/files/`, and any other path returns 301 to `/files/`
  [run]. Cosmos must forward `/files/*` without stripping the prefix, as designed.
- **`http.externalUrl`** builds share and download links and the page's canonical URL. It also
  feeds WebAuthn origins and the frontend `globalVars.externalUrl`. **It is not used for OIDC.**
  - Source: `internal/web/proxy.go:87-100`, `internal/database/share/frontend.go:71-94`,
    warning text at `pkg/settings/config.go:134` [src].
- **`externalUrl` must include `/files`.** `setupUrls` strips a trailing `/files/` from it, and
  links are built as `externalUrl + baseURL + "public/share/<hash>"`.
  - Source: `pkg/settings/config.go:514-518`, `internal/database/share/frontend.go:85-86`.
  - `https://host/files` gives `https://host/files/public/share/<hash>` [run].
  - `https://host` would give `https://host//files/…` [src].
- **`http.trustProxyHeaders: true` is required.** FileBrowser builds three things from
  `X-Forwarded-Host` and `X-Forwarded-Proto`: the OIDC `redirect_uri`, the cookie `Domain`, and
  the cookie `Secure` flag.
  - Without the flag it uses `Host` and the TLS state, which are wrong behind Cosmos.
  - The client IP becomes the right-most `X-Forwarded-For` entry.
  - Source: `internal/web/proxy.go:42-85`, `internal/web/oidc.go:197-200`,
    `internal/web/auth.go:235-237,561-574`, `internal/web/http.go:240-259` [run].

## 3. OIDC

### Config keys

All keys sit under `auth.methods.oidc`. Sources: `pkg/settings/auth.go:36-45,94-102,170-215`,
`pkg/settings/auth_groups.go:3-27` [src].

| Key | Meaning / default |
|---|---|
| `enabled` | turns OIDC on |
| `clientId`, `clientSecret` | env `FILEBROWSER_OIDC_CLIENT_ID` and `FILEBROWSER_OIDC_CLIENT_SECRET` override them (`pkg/settings/config.go:812-822`) [run] |
| `issuerUrl` | must equal the discovery `issuer` exactly: `http://<SPARK_HOSTNAME>`, no trailing slash [run] |
| `scopes` | default `openid email profile` |
| `userIdentifier` | default `preferred_username` (Cosmos username). The value must be a string claim |
| `groupsClaim` | default `groups`. Dotted paths work for nested claims |
| `adminGroup` | a group value that makes the user an admin |
| `userGroups` | allow-list. Users outside it are refused before an account is created (`internal/web/auth.go:146-174`) |
| `disableVerifyTLS` | irrelevant here: the issuer is reached over http |
| `logoutRedirectUrl` | returned by logout (below) |
| `createUser` | deprecated and ignored. Users are always auto-created |

If `adminGroup` is empty, new users take `userDefaults.account.permissions.admin` instead
(`internal/web/oidc.go:430-433`).

### Flow

- **Start of login: `GET /files/api/auth/oidc/login?redirect=<path>`.**
  - It sets two HttpOnly cookies, `filebrowser_oidc_state` and `filebrowser_oidc_redirect`. Both
    have `Path=/`, `Domain=<X-Forwarded-Host>` and `SameSite=Lax`, are `Secure` when the scheme is
    https, and last 10 minutes.
  - It then redirects to the Cosmos `authorization_endpoint`.
  - Source: `internal/web/oidc.go:22-39,210-244` [run].
- **Callback.** `redirect_uri` is `<scheme>://<X-Forwarded-Host><baseURL>api/auth/oidc/callback`.
  - Source: `internal/web/oidc.go:197-200`.
  - `X-Forwarded-Port` is ignored, so the callback never carries a port [run].
- **Token exchange.** golang.org/x/oauth2 auto-detects the client-auth method; it used
  `client_secret_basic` [run]. FileBrowser sends no PKCE, so it acts as a confidential client.
- **Claims.** The ID token is verified against the JWKS (RS256) and `aud` must contain `clientId`.
  FileBrowser falls back to the userinfo endpoint in three cases: verification fails, the
  `userIdentifier` claim is missing, or `adminGroup`/`userGroups` is set but the token carries no
  groups.
  - Source: `internal/web/oidc.go:313-371`.
  - With a Cosmos-shaped RS256 token the ID-token path was used and userinfo was never called [run].
- **How groups are parsed** (`internal/web/oidc.go:99-194`; string case at lines 167-177):
  - A string is split on commas and trimmed: `"admin"` becomes `["admin"]`.
  - An array, or an object's keys, also work.
  - Cosmos always sends `role` as `"admin"` or `"user"`, whatever the scope
    (`cosmos:authorizationserver/oauth2_user.go:40-44`).
  - Tested: `role: "admin"` gave admin=true; `role: "user"` gave admin=false [run].
- **Admin sync on every login is promote-only.** Code: if `isAdmin && !user.admin`, set admin.
  - Source: `internal/web/auth.go:121-129`.
  - Tested: bob logged in with role admin and was promoted. He then logged in with role user and
    stayed admin [run]. Demote in the UI or by `PATCH` (§9).
- **Login-method lock.** An existing account with a different `loginMethod` is refused
  (`internal/web/auth.go:130-134`). Never create password users with Cosmos usernames.
- **Usernames.** Must not be `anonymous` and must not contain `..`, `/` or `\`
  (`internal/database/users/users.go:52-65`) [src].
- **Session.**
  - The JWT is set in cookie `filebrowser_quantum_jwt`. It is HttpOnly, `Path=/`,
    `Domain=<X-Forwarded-Host>`, Secure when https, and `SameSite=Lax` at login.
  - Lifetime: `tokenExpirationHours` (default 2) plus a 30-minute cookie buffer. An open tab renews
    the session through `POST /files/api/auth/renew`.
  - Source: `internal/web/auth.go:503,561-574`, `internal/web/oidc.go:445-460`,
    `frontend/src/utils/auth.js:6` [run].
- **Logout.** `POST /files/api/auth/logout` returns `{"logoutUrl":"<oidc.logoutRedirectUrl>"}`, and
  the frontend navigates there unchecked.
  - Source: `internal/web/auth.go:247-299`, `frontend/src/utils/auth.js:190-211` [run].
  - UNVERIFIED: that Cosmos's `/cosmos-ui/logout` actually ends the Cosmos session. It is the
    `end_session_endpoint` Cosmos advertises (`cosmos:authorizationserver/oauth2_discover.go:80`).
- **Login page.** With password login off it shows a single button (`frontend.oidcLoginButtonText`).
  It redirects to OIDC automatically when opened with `?redirect=` (`frontend/src/views/Login.vue:255-264`).

### Cosmos side (from Cosmos source, reproduced by the mock)

- **Cosmos overwrites `X-Forwarded-Proto` with its own scheme.** In HTTP-only mode that is `http`,
  even though cloudflared sent `https`. Cosmos also sets `X-Forwarded-Host` to its configured
  hostname without the port, and passes the original `Host` through.
  - Source: `cosmos:proxy/routeTo.go:120-123,152,158-188`.
  - Effect when nothing else is set: `redirect_uri=http://<host>/files/api/auth/oidc/callback`,
    the cookies lose `Secure`, and an exact-match Cosmos client set to https rejects the login [run].
- **Fix: add an extra header on the Cosmos `/files` route.** Set
  `ExtraHeaders: {"X-Forwarded-Proto": "https"}`. Cosmos applies extra headers last
  (`cosmos:proxy/routeTo.go:204-224`; field `cosmos:utils/types.go:362`, yaml key `extra_headers`).
- **Recommended `/files` route:**
  - `UsePathPrefix: true` and `PathPrefix: /files`, with `StripPathPrefix: false`.
  - `Target: http://filebrowser:80`.
  - `AuthEnabled: false`, so anonymous share links work.
  - The extra header above.
- **OIDC client in Cosmos:**
  - ID `filebrowser`.
  - Redirect URI `https://<SPARK_HOSTNAME>/files/api/auth/oidc/callback`. Cosmos splits several
    URIs on commas (`cosmos:authorizationserver/oauth2.go:73`).
  - The secret must equal `FB_OIDC_CLIENT_SECRET`.

## 4. Sources and permissions

### Source keys

The keys under `server.sources[].config` (`pkg/settings/structs.go:228-244`):

| Key | Effect |
|---|---|
| `defaultEnabled` | the source is added to every new user, and re-added at each start for existing users (`cmd/user.go:140-145`). It is forced true when there is only one source (`pkg/settings/config.go:463-465`) |
| `createUserDir` | the user's scope becomes `defaultUserScope/<username>`, and the folder is created at first login. Applies to admins too. Source: `pkg/settings/user_scopes.go:13-37` [run] |
| `defaultUserScope` | default `/` |
| `readOnly` | blocks create, modify, delete and move **for everyone, including admins**. There is no per-user check. Source: `internal/web/resource.go:257,706` and others [run] |
| `private` | **no share links from this source, for anyone**. Source: `internal/web/share.go:332-334`, `internal/web/middleware.go:96-98` [src] |
| `denyByDefault` | hides everything unless an access rule allows it. Rules control visibility only, not read versus write [src] |
| `defaultPermissions` | see the next paragraph |

### Default permissions are global

There is **one template for every source.** FileBrowser takes it from the first source that sets
`defaultPermissions`, copies it to all sources at startup, and stores it in the database setting
`sourceAccessDefaults`.

- Source: `pkg/settings/config.go:491-505`, `pkg/settings/source_access.go:34-55`,
  `internal/state/source_access_defaults.go:26-62`.
- Tested: Shared's own `defaultPermissions` (read-only) were ignored, and new users got the Home
  template on Shared [run].
- With no template at all, the built-in default is `view` + `download` only
  (`pkg/settings/source_access.go:15-23`).
- File permissions per source are `view`, `download`, `create`, `modify` and `delete`
  (`internal/database/users/users.go:130-137`).
- `share`, `api`, `admin` and `realtime` are global account flags in
  `userDefaults.account.permissions`, all false by default (`pkg/settings/config.go:960-966`).

### What users get

- **OIDC-created users get the defaults.**
  - `getOrCreateAuthenticatedUser` calls `ApplyUserDefaults` before creating the user
    (`internal/web/auth.go:95-112`, `pkg/settings/settings.go:125-174`) [run].
  - **Admins are created like guests.** Defaults are applied while admin is still false, and
    `CreateUser` re-applies them, which resets admin to false. Only the login-time sync sets admin
    back to true.
    - Source: `pkg/settings/settings.go:133-161`, `internal/state/users.go:209`.
    - So an OIDC admin gets the template permissions, a `/<admin>` scope, share=false and api=false
      [run].
  - Only the password bootstrap admin gets `/` with full rights on every source
    (`internal/state/quick_setup.go:38-51`).
- **With the SPARK template, guests (verified)** [run]:
  - Home `/<name>` read-write; plain and chunked upload both work.
  - Shared readable; upload returns 403 `source is read-only`.
  - Creating a share returns 403.
  - SharedAdmin returns 403.
  - They cannot change their own permissions or scopes (403
    `cannot update privileged user fields`, `internal/web/users.go:398-402`).
- **Admins after first login:** admin=true, share=false, api=false, Home `/<admin>` read-write,
  Shared read-only [run].

### Admin steps

- **`auth.adminUsername` gives the owner share and api automatically.** At every start,
  `validateUserInfo` grants share and api to the user named in `auth.adminUsername`, if that user is
  already an admin. The login method does not matter.
  - Source: `cmd/user.go:77-89`, called from `cmd/root.go:147`.
  - The template sets it to `${SPARK_ADMIN_USERNAME}`.
  - Effect: the owner gets share and api on the first FileBrowser restart after their first login
    [run].
- **Every other admin needs a one-time grant.** Use the UI (Settings → User management → edit) or
  the API:

```http
PATCH /files/api/users?username=<admin>
{"which":["permissions"],"data":{"permissions":{"admin":true,"share":true,"api":true,"realtime":false}}}

PATCH /files/api/users?username=<admin>
{"which":["scopes"],"data":{"scopes":[
  {"name":"Home","scope":"/","permissions":{"view":true,"download":true,"create":true,"modify":true,"delete":true}},
  {"name":"Shared","scope":"/","permissions":{"view":true,"download":true,"create":true,"modify":true,"delete":true}},
  {"name":"SharedAdmin","scope":"/","permissions":{"view":true,"download":true,"create":true,"modify":true,"delete":true}}]}}
```

  - Both calls return 204 [run].
  - After the second call the admin sees every home, and can upload to Shared through
    `SharedAdmin` (`${SPARK_DATA}/shared` is also mounted at `/srv/shared-admin`).
  - A guest saw the admin's new file in Shared immediately [run].
- **Alternative without the second mount.** Admins add files to `SPARK_DATA/shared` on the laptop.
  To do that, remove `SharedAdmin` from the config and from compose.

## 5. Shares

- **Who can create shares.** Creating, editing, deleting and listing shares all require the global
  `share` permission. There is no admin bypass and no "admins only" switch.
  - Source: `internal/web/httpRouter.go:103-108`, `internal/web/middleware.go:710-717`.
  - SPARK's policy is therefore `userDefaults.account.permissions.share: false`, plus a share grant
    for admins (§4) [run].
- **Links are anonymous by default.** `disableAnonymous` is false unless set
  (`internal/database/share/share.go:10`, `internal/web/middleware.go:70-72`).
- **Optional controls on a share:**
  - a password, sent as header `X-SHARE-PASSWORD` by API clients (`internal/web/auth.go:462-501`);
    tested: 401 without it, 200 with it [run];
  - an expiry: `expires` plus `unit` (`seconds` / `minutes` / `days`, otherwise hours)
    (`internal/web/share.go:232-254`);
  - `allowedUsernames` and `downloadsLimit`.
- **Link formats** (`internal/database/share/frontend.go:71-94`) [run]:
  - page: `https://<SPARK_HOSTNAME>/files/public/share/<hash>`;
  - direct download: `https://<SPARK_HOSTNAME>/files/public/api/resources/download?hash=<hash>`;
  - password-protected direct download: `…/files/public/share/<hash>?download=true`.
  - Anonymous download returned 200 [run].
- **Sharing from a read-only source works.** Shares were created from Shared and from a guest's home
  (admin with Home scope `/`) [run].

## 6. Uploads

- **Settings** (`internal/database/users/users.go:275-280`, `pkg/settings/config.go:955-959`):
  - `userDefaults.fileLoading.uploadChunkSizeMb`: default 10;
  - `maxConcurrentUpload`: default 10;
  - `downloadChunkSizeMb`: default 0.
- **What the web UI does with the chunk size** (`frontend/src/utils/upload.js:516-520,694`):
  - The value is in MiB.
  - Files at least that large are sent in chunks; smaller files go in one request.
  - **`0` means never chunk.** Through Cloudflare, any file over 100 MB then fails.
- **Users can change it themselves.** It is a per-user value, editable in the upload dialog
  (`frontend/src/components/prompts/Upload.vue:23`). A guest set it to 200 MB and got 204 [run].
- **Lock it once as admin** (stored in the database, not the config) [run]:
  - Call `PATCH /files/api/settings/user-defaults` with body
    `{"enforced":{"fileLoading":{"uploadChunkSizeMb":true}}}`.
  - Result: every user is reset to the default, and later changes return 403
    `setting is enforced by administrator`.
- **Config values still apply to new users only.** `userDefaults` in config are re-applied over the
  database defaults at every start (`internal/state/user_defaults.go:56-67`), but existing users keep
  their own values.
- **Tested:** 25 MB in 10 MiB chunks through the mock proxy, checked with sha256 [run].

## 7. Password login off, and recovery

- **Turning password login off.** `password.enabled` defaults to **true**, so set it to false
  explicitly (`pkg/settings/config.go:892-896`).
  - With only OIDC on, the log shows `Auth Methods : [oidc]`.
  - No bootstrap admin is created.
  - `POST /files/api/auth/login` returns 401.
  - Source: `pkg/settings/config.go:528-582`, `internal/state/quick_setup.go:20-25`,
    `internal/web/middleware.go:409-427` [run].
- **Recovery.** The CLI also runs OIDC discovery, and **exits when Cosmos is down** [run]. Keep a
  recovery config: the same file with `password.enabled: true` and `oidc.enabled: false`. Then:

```sh
docker compose stop filebrowser
docker compose run --rm filebrowser -c /home/filebrowser/config/recovery.yaml user set rescue --password 'Long-Passw0rd'
docker compose run --rm filebrowser -c /home/filebrowser/config/recovery.yaml user promote rescue
# start once with the recovery config, log in as rescue, fix users; then restore the normal config and restart
```

- **`user set <new> --admin` does not create an admin in 2.0.2.** `CreateUser` re-applies the
  defaults (`internal/state/users.go:209`, `pkg/settings/settings.go:134`). Seen: admin=false.
  Always follow it with `user promote` [run].
- **Never run `user set` on an OIDC user.** It converts the account to password login
  (`cmd/cli.go:116-121`), and that person's OIDC login then fails until an admin sends
  `PATCH {"which":["loginMethod"],"data":{"loginMethod":"oidc"}}`.
- **Run the CLI only while the server is stopped.** The server keeps users in memory.
- **Commands** (`cmd/kong.go:44-57,115-124`): `user set <name> --password [value]`,
  `user promote <name>`, `set rule`, `setup`, `version`.

## 8. Environment variables

- **Expansion in config.yaml.** After the YAML is parsed, every string value goes through Go's
  `os.ExpandEnv`, which handles `$VAR` and `${VAR}`.
  - Unset variables become empty.
  - A value that is only a reference is converted to bool or number.
  - So no value may contain any other literal `$`.
  - Source: `pkg/settings/config.go:651-653`, `pkg/settings/config_env.go:15-60` [run].
  - Parsing is strict: unknown keys are fatal (`pkg/settings/config.go:693`).
- **Variables FileBrowser reads** (`pkg/settings/config.go:795-847` unless noted):
  - `FILEBROWSER_CONFIG`;
  - `FILEBROWSER_DATABASE_PATH`; the old `FILEBROWSER_DATABASE` is fatal
    (`pkg/settings/database_paths.go:35-40`);
  - `FILEBROWSER_OIDC_CLIENT_ID` and `FILEBROWSER_OIDC_CLIENT_SECRET`, which override the file;
  - `FILEBROWSER_ADMIN_PASSWORD`, used by password bootstrap only;
  - `FILEBROWSER_JWT_TOKEN_SECRET` (the session signing key). **Do not set it after the first
    start:** startup fails if it differs from the stored key
    (`internal/state/auth_signing_key.go:48`);
  - `FILEBROWSER_FFMPEG_PATH`, which the image sets;
  - `FILEBROWSER_SQL_WAL`;
  - `FILEBROWSER_DISABLE_AUTOMATIC_BACKUP`;
  - `FILEBROWSER_DEVMODE` and `FILEBROWSER_PLAYWRIGHT_TEST`, which must never be set.
- **Template placeholders:** `SPARK_HOSTNAME`, `FB_OIDC_CLIENT_SECRET`, `SPARK_ADMIN_USERNAME`.

## 9. REST API for tests (base `/files`)

### Authentication

- **Token locations.** FileBrowser reads the session JWT from cookie `filebrowser_quantum_jwt`. It
  also accepts the same token as `Authorization: Bearer <jwt>` or `?auth=<jwt>`
  (`internal/web/auth.go:34-79`) [run].
- **Getting the cookie means doing the OIDC flow:**
  1. `GET /files/api/auth/oidc/login`, keeping the `filebrowser_oidc_state` cookie.
  2. Log in at the Cosmos authorize URL.
  3. `GET /files/api/auth/oidc/callback?code&state` with that cookie. The response is 302 to
     `/files/` and sets the session cookie.
- **The cookie is scoped to `SPARK_HOSTNAME`.** A client that connects some other way must send the
  `Cookie` header itself, as `spike/fb-research/e2e_test.py` does.
- **Rate limit.** OIDC login and callback allow 60 requests a minute, burst 20, per client IP.
  Behind the tunnel every user shares cloudflared's IP (`internal/web/http.go:93-94,372-379`).
- **API tokens.** `POST /files/api/auth/token?name=&days=&permissions=` creates one; the caller needs
  the `api` permission (`internal/web/api.go:21-34`).

### Endpoints

| Purpose | Request | Verified |
|---|---|---|
| Who am I | `GET /files/api/users?username=self` | [run] |
| List users (admin) | `GET /files/api/users` | [run] |
| Create user (admin) | `POST /files/api/users` with body `{"which":[],"data":{…}}`, returns 201 (`internal/web/users.go:167-203`) | [src] |
| Update user | `PATCH /files/api/users?username=<u>` with body `{"which":[fields],"data":{…}}`, returns 204 | [run] |
| Delete user | `DELETE /files/api/users?username=<u>` | [src] |
| List directory | `GET /files/api/resources?source=Home&path=/` | [run] |
| Download | `GET /files/api/resources/download?source=Home&file=/a.txt` (repeat `file` to get a zip) | [run] |
| Upload in one request | `POST /files/api/resources?source=Home&path=/a.txt[&override=true]`, raw body | [run] |
| Chunked upload | Same POST once per chunk, with headers `X-File-Chunk-Offset`, `X-File-Total-Size` and `X-File-Upload-Session` (`[A-Za-z0-9_-]{1,128}`). It returns `{"offset","total","complete"}` and assembles `<dest>.<md5>.uploading.tmp`. Source: `internal/web/resource.go:781-955`, `internal/web/upload_session.go:12,73-90`, `internal/web/upload_validate.go:16-22` | [run] |
| Create folder | `POST /files/api/resources?source=Home&path=/dir&isDir=true` | [src] |
| Create share | `POST /files/api/share` with `{"source":"Home","path":"/alice/x.txt","expires":"7","unit":"days","password":"…"}`. The response includes `hash`, `shareURL` and `downloadURL` | [run] |
| List / delete shares | `GET /files/api/share/list`, `DELETE /files/api/share?hash=<h>` | [run] / [src] |
| Anonymous share info | `GET /files/public/api/resources?hash=<h>[&path=]` | [run] |
| Anonymous download | `GET /files/public/api/resources/download?hash=<h>` | [run] |
| Share page (HTML) | `GET /files/public/share/<h>` | [run] |
| Swagger UI (admin) | `/files/swagger/index.html` | [run] |

## 10. Things that break behind a proxy, a path, or an http issuer

1. **Cosmos turns `X-Forwarded-Proto` into `http`.** The callback URL and the cookie `Secure` flag
   are then wrong. Fix it with the route extra header (§3) [run].
2. **Cookies are visible to every app on the hostname.** They are set with `Path=/` and
   `Domain=SPARK_HOSTNAME`, so Cosmos and any other app routed on that hostname receive the
   FileBrowser session cookie. Keep untrusted path-based apps off that hostname [run].
3. **Login never works through the local port** (`http://localhost:SPARK_HTTP_PORT/files`). Cosmos
   always sends `X-Forwarded-Host` set to the public hostname, so the cookies are scoped to it and
   the callback goes to the public URL [src].
4. **The http issuer sends the browser to `http://<host>/cosmos-ui/openid`.** That hop leaves https.
   Turn on Cloudflare "Always Use HTTPS"; otherwise the Cosmos login can travel in cleartext between
   the browser and Cloudflare. FileBrowser's own back-channel to Cosmos is plain http inside Docker,
   which is fine. UNVERIFIED: browser and Cloudflare behaviour.
5. **Cosmos replaces FileBrowser's CSP.** With header hardening on, Cosmos removes FileBrowser's
   nonce-based Content-Security-Policy and sets only `frame-ancestors 'self'`
   (`cosmos:proxy/routeTo.go:245-252`, `cosmos:utils/middleware.go:151-164`). The UI should still
   work, but loses FileBrowser's script restrictions. `DisableHeaderHardening` on the route keeps
   FileBrowser's CSP. UNVERIFIED in a browser.
6. **FileBrowser exits at startup if Cosmos is not up** (§1). The OIDC provider is cached after
   startup; a later Cosmos restart only breaks logins while it is down. UNVERIFIED with real Cosmos.
7. **A hostname change breaks login until everything is updated.** This applies to quick-tunnel
   mode: `issuerUrl`, `externalUrl` and the Cosmos redirect URI all contain the hostname. After a
   change, update the env or re-render the config, recreate the container, and update the Cosmos
   client.
8. **Slow Cosmos logins fail.** The OIDC state cookie lasts 10 minutes, so a Cosmos login plus MFA
   that takes longer ends in `invalid OIDC state` (400).
9. **Everyone shares one rate-limit bucket and one IP in activity logs** (the cloudflared IP). The
   rate limit is §9's.
10. **POSTs without `Referer` may be blocked.** If the Cosmos route has `BlockAPIAbuse` on, Cosmos
    blocks those POSTs (`cosmos:proxy/routerGen.go:175-176`). Test clients should send `Referer`.
    UNVERIFIED.

## Optional automation channel (tested, not enabled in the template)

`auth.methods.jwt` can give the `spark` CLI admin access without a browser. Keys: `enabled: true`,
`secret: "<random>"`, `algorithm: HS256`, `groupsClaim: groups`, `adminGroup: spark-automation`.

- **How it works.** A request with header `X-JWT-Assertion: <HS256 JWT {sub, groups, exp}>`
  auto-creates an admin service user (`loginMethod: jwt`) and can call any admin API.
- **Tested:** listing users and `PATCH` returned 200 and 204, and a forged signature returned 403
  [run].
- **Use:** grant admin scopes and share, or lock the chunk size, without a browser.
- **Cost:** a second admin credential, kept in `.env`.
- Source: `pkg/settings/auth.go:290-317`, `internal/auth/jwt.go:47-104`,
  `internal/web/middleware.go:449-460,521-581`.

## Suggested compose service (not tested with real Cosmos)

```yaml
filebrowser:
  image: gtstef/filebrowser:${FILEBROWSER_VERSION}
  environment:
    FILEBROWSER_CONFIG: /home/filebrowser/config/config.yaml
    SPARK_HOSTNAME: ${SPARK_HOSTNAME}                # only if the template is mounted unrendered
    SPARK_ADMIN_USERNAME: ${SPARK_ADMIN_USERNAME}
    FB_OIDC_CLIENT_SECRET: ${FB_OIDC_CLIENT_SECRET}
  volumes:
    - filebrowser-data:/home/filebrowser/data
    - ./config/filebrowser/config.yaml:/home/filebrowser/config/config.yaml:ro   # file only, own dir
    - ${SPARK_DATA}/users:/srv/users
    - ${SPARK_DATA}/shared:/srv/shared
    - ${SPARK_DATA}/shared:/srv/shared-admin     # drop together with the SharedAdmin source
  depends_on:
    cosmos: { condition: service_healthy }
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "curl", "-fsS", "http://localhost:80/files/health"]
    interval: 30s
    timeout: 5s
    retries: 3
```

## UNVERIFIED / open

- **The whole flow against real Cosmos 0.23.4.** The mock copies Cosmos's discovery, claims and proxy
  headers from source. Cosmos's consent page, MFA, cookies and timing were not exercised.
- **`/cosmos-ui/logout`** as the logout target.
- **Browser-level behaviour** (CSP under Cosmos hardening, the http authorize hop, PWA).
- **SmartShield or Cosmos route timeouts** on long downloads and many upload chunks.
