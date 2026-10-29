# tests/ — end-to-end smoke tests

Scripts run against a live install to prove the promises in the design hold: guest sees only their
home + read-only shared folder, upload to shared fails, share links open without login, data survives
`docker compose down && up`. Unit tests for the CLI live in `tools/spark-cli/tests/`.
