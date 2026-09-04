# GA401 Multica v0.4.39 upgrade boundary

This kit describes a bounded, operator-run upgrade of the existing `multica`
Compose project. It preserves the invite-only backend behavior, Skill picker,
native voice, browser/account registrations, provider logins, and all data.

## Fixed target

- Host: `ga401` (`10.0.0.101`), public endpoint `https://agent.hankee.com`.
- Source root: `/home/marck/services/multica/app`.
- Compose project/file: `multica`, `docker-compose.selfhost.yml`.
- Current images: `multica-backend-invite-only:560f01203`,
  `multica-web:560f01203`; PostgreSQL is `pgvector/pgvector:pg17`.
- Data volumes: `multica_pgdata`, `multica_backend_uploads`.
- GA401 runtime volumes retained exactly: `multica-ga401-runtime_runtime-home`,
  `multica-ga401-runtime_cli-tools`, and `multica-ga401-runtime_browser-tools`.

The candidate is built from source commit `f42a0a4678f3aa8ecba981f542d3ef3b66996249`
with the reviewed invite-only patch applied. Candidate image tags are bound to
the source hash and `0.4.39-ga401`; no floating tags are accepted.

## Phases

`upgrade.py` accepts `preflight`, `build`, `rehearse`, `activate`, and `verify`.
Each invocation records phase and evidence in a private state file and stops on
the first failure. It never retries, resumes silently, changes Caddy/router/DNS,
or manages the existing GA401 CLI updater.

`preflight` records Compose/config/image identity, database schema version and
counts, runtime volumes, disk space, and a zero-active admission snapshot.
`build` requires the exact source archive and Dockerfile inputs and records image
IDs. The runtime image derives from the exact old runtime image and copies only
the candidate `/app/multica` binary to `/usr/local/bin/multica`. `rehearse` takes
a logical `pg_dump`, restores it into a temporary PostgreSQL 17 container with
no host port or production volume, and runs migrations 441–450 there.
`activate` rechecks original config/image/volume contracts, stops only the
backend/frontend and named runtime containers, captures the final consistent
database dump after stopping, backs up relevant volumes, and starts candidates
through separate image overrides while retaining the original env and PostgreSQL.
The app checks complete before reopening the runtime. `verify` then checks
`/health`, `/readyz`, migration completion, durable row identities, retained
volumes, the running runtime CLI version and the public endpoint.

There is no `down -v`, prune, automatic rollback, or PostgreSQL recreation.
After any phase failure the state is `FAILED_NEEDS_DECISION`; the operator
must choose the retained exact images/Compose and same volumes for rollback.

## Backup and runtime boundary

Activation creates private database and volume backups under the immutable release
directory with restrictive permissions. Existing env files, PostgreSQL, runtime
home, browser tools, provider logins, and registrations are retained. The
existing guarded provider updater remains unchanged; it updates provider tools,
not Multica. This one-shot controller does not enable recurring updates.

The backend entrypoint runs `./migrate up` before serving and uses the migration
advisory lock. Starting the candidate backend is the migration authority; do not
run an independent migration command against production. Confirm `/health` and
`/readyz` show candidate source, database, and migrations `ok` before any runtime
reopen.

Future automatic source updates must merge upstream with conflict resolution and
pause for required review; they must not rsync source trees. This one-shot
controller does not implement recurring automation.
