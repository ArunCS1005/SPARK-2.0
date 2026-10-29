# Accounts, folders and share links

## Accounts

Cosmos keeps the accounts; every app signs in through it. The free tier allows **5 accounts**,
yours included. Invites that have not been used yet count too.

```bash
docker compose run --rm spark user add bob            # a normal account
docker compose run --rm spark user add carol --admin  # another admin
docker compose run --rm spark user list               # who exists, invite status, two-factor
docker compose run --rm spark user remove bob         # asks first
```

`user add` prints an **invite link**. Send it privately: whoever opens it first chooses the
password. It works once and expires after 7 days. If it expires, remove the account and add it again.

Removing an account keeps that person's files (`SPARK_DATA/users/<name>`) and FileBrowser's own
record of them. If someone else later gets the same name, they see those files, so delete them
(Files → Settings → User management, and the folder) unless the same person is coming back.

The owner account made by setup cannot be removed, and neither can the last admin.

## Who sees which folder in Files

| In Files | Folder in `SPARK_DATA` | Who | Can |
|---|---|---|---|
| **Home** (opens first) | `users/<name>/` | Everyone gets their own, made at first sign-in | Read and write. Nobody else's. |
| **Shared** | `shared/` | Everyone | Look and download only, admins included |
| **SharedAdmin** | `shared/` (same folder) | The owner (set up by `spark setup`); other admins once granted | Read and write |

Shared and SharedAdmin are the same folder seen two ways. Everyone sees it read-only as **Shared**;
the owner also gets **SharedAdmin**, where uploading, renaming, editing and deleting work, and the
changes show up in everyone's Shared right away. FileBrowser cannot make one folder writable for
admins only, which is why it appears twice.

Other ways to put files in Shared: copy them into `SPARK_DATA/shared` on the laptop, or give another
admin the SharedAdmin folder: Files → Settings → User management → edit the admin → add SharedAdmin.

Admins in Cosmos are admins in Files. Normal accounts cannot create share links.

## Share links

The owner can share any file or folder from the first sign-in: right-click → Share. Choose an
expiry and, if you like, a password. The link works for anyone, with no account. Other admins get
the right in Files → Settings → User management.

How the owner gets these rights: `spark setup` prepares the owner's Files account before their
first sign-in (or updates it if it exists) through FileBrowser's admin API, as a service account
called `spark-cli`. That account has no password and cannot sign in; its empty folder
`users/spark-cli` can be ignored.

## Two-factor login

Turn it on for your own account in the dashboard (account menu). `spark user list` shows who has
it. To make it mandatory for everyone: `docker compose run --rm spark setup --require-mfa`.
