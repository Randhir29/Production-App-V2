# ProDash API runbook

Operational guide for the FastAPI backend (see `agent.md` for the rules it implements).

## 1. What runs where

| Piece | Notes |
|---|---|
| API | `uvicorn app.main:app` (Docker image: `Dockerfile`). Stateless; run several replicas if needed. |
| Central DB | `CENTRAL_DATABASE_URL`. Owned by this app, migrated with Alembic. Holds plants, carousel config, users, refresh tokens. |
| Plant DBs | One PostgreSQL per plant, **read-only**. Credentials live in the central `plants` table, never in the repo or the image. |
| Static config | `config/static_plant_config.yaml`, baked into the image. Loaded at startup and by `/health/ready`. |

Windows hosts: start uvicorn with `--loop app.core.db:selector_loop_factory` (psycopg async needs the selector loop).
Behind a reverse proxy: set `TRUSTED_PROXIES`; the app resolves `X-Forwarded-For` itself, so keep uvicorn's `--no-proxy-headers`.

## 2. First-time setup (new environment)

Run from the repo with the environment variables for the target environment set (`.env` or real env).

1. `alembic upgrade head`: create/upgrade the central schema (roles `admin`, `officer`, `viewer` are seeded).
2. `python -m app.cli.main migrate-central-data --confirm`: copy plants, carousals, breaks, users and group memberships from the legacy MySQL (`LEGACY_MYSQL_URL`). Run without `--confirm` first and read the skipped-row warnings.
3. `python -m app.cli.main import-plant-connections --php /secure/path/Database.php --confirm`: write each plant's host, user, password and database from the legacy `Database.php` into `plants`. Dry run first. Unmatched plants are listed; `--create-missing` inserts them with an empty name and zone. A port other than `PLANT_DB_PORT` is only reported (there is no per-plant port column).
4. `python -m app.cli.main import-carousel-config --csv /secure/path/lpg_carousal_details_corrected.csv --confirm`: upsert `carousals` and replace `breaks` per carousel, matching `plants.erp_id = sap_id`. Rows that cannot be matched or parsed are listed; carousels without breaks are reported (V2 reports return 409 for them, D-06).
5. `python -m app.cli.main export-static-config --csv ... --confirm`: regenerate `carousal_config` in `config/static_plant_config.yaml` from the same CSV (short names via `plants.erp_id`). Commit the YAML, rebuild the image.
6. `python -m app.cli.main create-admin --username NAME --email ADDRESS`: password is prompted (or `--password-stdin`). Never pass it on the command line.
7. `python -m app.cli.main hash-api-key --generate`: put the digest in `SERVICE_API_KEYS` (JSON list) and give the key to the mail job as `X-API-Key`.

Keep `Database.php` and the CSV out of git and out of the image (`.gitignore` and `.dockerignore` already exclude them).

## 3. Configuration that matters in production

| Variable | Guidance |
|---|---|
| `APP_ENV=production` | Swagger/ReDoc/openapi are disabled unless `DOCS_ENABLED=true`. |
| `AUTH_ENABLED=true` | Keep on. `false` is only for a closed network during migration. |
| `JWT_SECRET_KEY` | At least 32 random characters; rotating it logs everyone out. |
| `PLANT_DB_CONNECT_TIMEOUT_S` | 10 s default. |
| `PLANT_DB_STATEMENT_TIMEOUT_MS` | 120000; long V2 ranges are heavy. Raise only with the plant DBA. |
| `PLANT_MAX_CONCURRENCY` | Concurrent plant queries per plant per process (default 4). Total load on a plant is this times the replica count. |
| `PLANT_ENGINE_CACHE_MAX`, `PLANT_ENGINE_TTL_S` | Engine cache bound (64) and lifetime (3600 s). |
| `SLOW_QUERY_MS` | Statements slower than this log a WARNING (default 5000). |
| `RESPONSE_CACHE_TTL_S` | 0 = off. 15-60 is reasonable for dashboard polling; applies only to the under-performing and inaccurate scales reads, never to reports. |
| `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES` | 5 failures lock an account for 15 minutes. |

## 4. Health and monitoring

* `GET /api/v1/health/live`: process is up (always 200).
* `GET /api/v1/health/ready`: central DB answers and the static config validates; body lists the two `checks`. Use it for load-balancer readiness.
* Logs are JSON on stdout (`LOG_JSON=true`). Useful loggers:
  * `prodash.access`: `method`, `path`, `status`, `duration_ms`, `request_id` (query strings are not logged). Responses echo `X-Request-ID`.
  * `prodash.core.db`: every plant connection with `latency_ms`, success or failure; `slow query` warnings with a truncated statement and never parameters.
  * `prodash.services.*`: guards firing (D-05, D-06 warnings), lockouts, refresh-token replay.
* Alert suggestions: sustained 5xx on `/plants/*/reports/*`, `plant db connection failed` bursts for one host, `refresh token replay detected`, `account locked`, `/health/ready` 503.

## 5. Load test (55 plants x V2 in parallel)

```
LOAD_TOKEN=<access token of a viewer/officer/admin> \
python -m scripts.load_v2 --base-url https://prodash.example --from-date 2026-01-01 \
    --to-date 2026-01-07 --p95-target-s 60 --max-error-rate 0.02
```

It fires V2 for every plant in `GET /plants` at once (`--concurrency` 55), prints p50/p95/max and the HTTP status counts, and exits non-zero when a target is missed. Run it against staging with production-like plant data, then watch `prodash.core.db` for connection failures and confirm the connection counts on the plant servers drop back after the run (no leaks). The p95 target has to be agreed with the product owner.

## 6. Incident playbook

| Symptom | Likely cause | Action |
|---|---|---|
| 502 `PLANT_UNREACHABLE` for one plant | Plant server/network down, wrong credentials | Check `plant db connection failed` log for the host. Test with `/plants/{plant}/connectivity`. Fix credentials with `import-plant-connections` or the DB row. |
| 502 `PLANT_FUNCTIONS_UNAVAILABLE` | V2 helper functions missing and the plant user cannot create them | A DBA creates `getGapBetweenTimes` and `getEndGapForPhase` (agent.md §5.7.4) once, or grants `CREATE` on the schema to the plant user. |
| 409 `PLANT_CONFIG_INCOMPLETE` | Plant has no carousel config, a carousel without shift times, or without breaks (D-06) | Fix the CSV or the `carousals`/`breaks` rows and re-run `import-carousel-config`. |
| `Unknown Plant` on connectivity | Plant not in static `plants` (D-13) or no credentials in `plants` | Add to the YAML and rebuild; run `import-plant-connections`. |
| User cannot log in | Locked, inactive, banned, deleted | Same 401 for all of them by design. Unlock: `UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE username = '...'`. Check `active`, `status`, `deleted_at`. |
| Everyone gets 401 | `JWT_SECRET_KEY` changed, clock skew | Users log in again; check NTP. |
| Dashboard slow | Plant query slow | Look for `slow query` warnings; check the plant indexes (agent.md §12.2); consider `RESPONSE_CACHE_TTL_S`. |

## 7. Credentials and secrets

* Plant DB passwords are stored in plain text in `plants.db_password` (D-23). Until column encryption or a secret store is approved: restrict access to the central DB, use read-only roles on every plant, and rotate the passwords that were in the legacy `Database.php`.
* Do not paste `Database.php`, the CSV, tokens or API keys into tickets or chat. The import commands print names and counts only.
* API keys are stored only as SHA-256 digests; losing a key means generating a new one with `hash-api-key --generate`.

## 8. Deploy and rollback

1. Build the image, run `alembic upgrade head` (additive migrations; roll back with `alembic downgrade -1` if needed), then roll the API.
2. A bad config import can be undone by re-running the import with the previous file; the importers are idempotent and print what they changed.
3. Rolling back the image is safe as long as the migration revision it expects is already applied.
