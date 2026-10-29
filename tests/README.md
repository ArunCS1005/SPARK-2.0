# tests/ — end-to-end smoke test

`tests/smoke/run.sh` installs SPARK on a throwaway compose project (`spark-e2e` by default) in
laptop-only mode without publishing any port, then checks the promises in the design from inside the
project network, the way a browser would (`flow.py`):

- the owner and a guest sign in to Files through Cosmos (OpenID);
- the guest gets `Home/<name>`, cannot write to Shared, cannot open SharedAdmin, cannot share;
- a 25 MB upload in 10 MB chunks arrives intact;
- an owner's share link works without any session;
- `spark setup` run again changes nothing; `spark doctor` passes;
- backup → change → restore brings back the earlier accounts.

It removes only what it created (`down --volumes` for its own project) and refuses to run as
project `spark`. Needs Docker and Compose 2.24+; takes about two minutes.

```bash
tests/smoke/run.sh
```

Unit tests for the CLI live in `tools/spark-cli/tests/` (see `docs/development.md`).
