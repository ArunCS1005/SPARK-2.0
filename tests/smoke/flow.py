"""Browser-like checks against a running SPARK, run by tests/smoke/run.sh inside the `spark` CLI
container (on the project network, where the cosmos alias answers for SPARK_HOSTNAME).

Signs in to Files through Cosmos's OpenID the way a browser does (Cosmos login, FileBrowser's
OpenID start, Cosmos's consent form, callback), then checks what each account may do.
`flow.py first-sign-in` only signs the owner in (used again after a restore).
Environment: SPARK_HOSTNAME, OWNER, OWNER_PASSWORD, GUEST, GUEST_PASSWORD, GUEST_INVITE_KEY."""

import os
import sys
import uuid
from urllib.parse import urlsplit

import httpx

HOST = os.environ["SPARK_HOSTNAME"]
BASE = f"http://{HOST}"
FILES = f"{BASE}/files"
failures: list[str] = []


def check(what: str, ok: bool, detail: object = "") -> None:
    print(
        f"[{'ok' if ok else 'fail'}]{'   ' if ok else ' '}{what}"
        + ("" if ok else f": {detail}")
    )
    if not ok:
        failures.append(what)


def register(nickname: str, password: str, key: str) -> None:
    body = {"nickname": nickname, "password": password, "registerKey": key}
    response = httpx.post(f"{BASE}/cosmos/api/register", json=body)
    check(
        f"{nickname} registers with the invite key",
        response.status_code == 200,
        response.text,
    )


def sign_in(nickname: str, password: str) -> httpx.Client:
    client = httpx.Client(timeout=60)
    login = client.post(
        f"{BASE}/cosmos/api/login", json={"nickname": nickname, "password": password}
    )
    check(f"{nickname} signs in to Cosmos", login.status_code == 200, login.text)
    start = client.get(f"{FILES}/api/auth/oidc/login", params={"redirect": "/files/"})
    target = urlsplit(start.headers.get("location", ""))
    consent = client.post(
        f"{BASE}/oauth2/auth?{target.query}",
        data={"client_id": "filebrowser", "scopes": ["openid", "email", "profile"]},
    )
    callback = consent.headers.get("location", "")
    check(
        f"{nickname}: Cosmos approves the Files sign-in",
        consent.status_code == 303,
        consent.text,
    )
    done = client.get(callback)
    check(f"{nickname} signs in to Files", done.status_code == 302, done.text)
    return client


def upload(client: httpx.Client, source: str, path: str, data: bytes) -> httpx.Response:
    params = {"source": source, "path": path}
    return client.post(f"{FILES}/api/resources", params=params, content=data)


def chunked_upload(client: httpx.Client, path: str, size: int, chunk: int) -> bool:
    data = os.urandom(size)
    session = uuid.uuid4().hex
    for offset in range(0, size, chunk):
        headers = {
            "X-File-Chunk-Offset": str(offset),
            "X-File-Total-Size": str(size),
            "X-File-Upload-Session": session,
        }
        part = data[offset : offset + chunk]
        response = client.post(
            f"{FILES}/api/resources",
            params={"source": "Home", "path": path},
            content=part,
            headers=headers,
        )
        if response.status_code != 200:
            return False
    back = client.get(
        f"{FILES}/api/resources/download", params={"source": "Home", "file": path}
    )
    return back.content == data


def main() -> int:
    owner, guest = os.environ["OWNER"], os.environ["GUEST"]
    register(guest, os.environ["GUEST_PASSWORD"], os.environ["GUEST_INVITE_KEY"])
    admin = sign_in(owner, os.environ["OWNER_PASSWORD"])
    visitor = sign_in(guest, os.environ["GUEST_PASSWORD"])

    me = visitor.get(f"{FILES}/api/users", params={"username": "self"}).json()
    scopes = {s["name"]: s["scope"] for s in me.get("scopes", [])}
    check(
        "guest gets Home/<name> and Shared",
        scopes == {"Home": f"/{guest}", "Shared": "/"},
        me,
    )
    check("guest is not an admin", me.get("permissions", {}).get("admin") is False, me)

    check(
        "guest writes own Home",
        upload(visitor, "Home", "/a.txt", b"hi").status_code == 200,
    )
    shared = upload(visitor, "Shared", "/a.txt", b"x")
    check("guest cannot write Shared", shared.status_code == 403, shared.text)
    other = visitor.get(
        f"{FILES}/api/resources", params={"source": "SharedAdmin", "path": "/"}
    )
    check("guest cannot open SharedAdmin", other.status_code == 403, other.text)
    share = visitor.post(
        f"{FILES}/api/share", json={"source": "Home", "path": "/a.txt"}
    )
    check("guest cannot create share links", share.status_code == 403, share.text)
    check(
        "25 MB upload in 10 MB chunks",
        chunked_upload(visitor, "/big.bin", 25 << 20, 10 << 20),
    )

    rights = admin.get(f"{FILES}/api/users", params={"username": "self"}).json()
    sources = [s["name"] for s in rights.get("scopes", [])]
    check(
        "owner has Home, Shared and SharedAdmin",
        sources == ["Home", "Shared", "SharedAdmin"],
        rights,
    )
    check(
        "owner may share from the first sign-in",
        rights["permissions"].get("share") is True,
    )
    put = upload(admin, "SharedAdmin", "/notes.txt", b"for the group")
    check(
        "owner writes to Shared through SharedAdmin", put.status_code == 200, put.text
    )
    seen = visitor.get(
        f"{FILES}/api/resources/download",
        params={"source": "Shared", "file": "/notes.txt"},
    )
    check(
        "guest reads the owner's file in Shared",
        seen.content == b"for the group",
        seen.text,
    )
    gone = admin.delete(
        f"{FILES}/api/resources", params={"source": "SharedAdmin", "path": "/notes.txt"}
    )
    check("owner deletes from Shared", gone.status_code == 200, gone.text)

    upload(admin, "Home", "/public.txt", b"for everyone")
    made = admin.post(
        f"{FILES}/api/share",
        json={"source": "Home", "path": "/public.txt", "expires": "1", "unit": "days"},
    )
    link = made.json().get("hash") if made.status_code == 200 else None
    check("owner creates a share link", bool(link), made.text)
    if link:
        anonymous = httpx.get(
            f"{FILES}/public/api/resources/download", params={"hash": link}
        )
        check(
            "share link works without a session", anonymous.content == b"for everyone"
        )

    print(
        f"\n{len(failures)} failure(s)." if failures else "\nAll browser checks passed."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["first-sign-in"]:
        sign_in(os.environ["OWNER"], os.environ["OWNER_PASSWORD"])
        sys.exit(1 if failures else 0)
    sys.exit(main())
