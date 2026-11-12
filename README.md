# Sales intelligence data pipeline

A local Python/PostgreSQL pipeline for **synthetic CRM data**. It ingests accounts,
opportunities, activities, and explicit stage events, then publishes SQL reporting tables for
the existing Python sales application. All supplied names, IDs, dates, amounts, and events
are fictional. Fixture totals are correctness examples, not business results or scale claims.

## Assessment and implementation milestones

The workspace initially contained no files or instructions. Both sibling applications were inspected:

| Project | Existing conventions | Integration decision |
| --- | --- | --- |
| `../python-sales-intelligence` | Python 3.14, FastAPI, SQLAlchemy async sessions, Alembic, PostgreSQL, UUIDs, UTC, decimal USD, optional `X-API-Key`; single tenant | Separate schemas in the same PostgreSQL database; read-only routes use its existing session and authentication dependency |
| `../react-sales-intelligence` | React 19, TypeScript, generated OpenAPI types, relative `/api/v1` requests | No frontend changes needed for this scope |

The backend has CRM tables, CSV jobs, Celery/Redis, current-state analytics, and JSON CRM audit
activities. It has no dedicated warehouse, ingestion cursor, or explicit stage-event table.
Neither application supplies a durable external CRM change feed. This project provides a local
append-only synthetic source instead. It does **not** manufacture history from backend current state.

1. Verified accounts/opportunities → raw/staging → PostgreSQL → $3,000 open-pipeline fixture.
2. Added activity, explicit history, reporting, atomic publication, recovery, and real database tests.
3. Added authenticated backend routes, interval scheduling, a measured recovery demo, and contracts.

See [architecture](docs/architecture.md), [metric definitions](docs/metrics.md),
[source and database contracts](docs/contracts.md), [backend integration](docs/integration.md),
and [verification evidence](docs/verification.md).

## Start locally with Docker

Requirements: Docker with Compose v2. No host Python, Redis, cloud account, or API credential is
needed. PostgreSQL binds to loopback port **55432**, separate from the application's default 5432.

Start Docker Desktop before running these commands. On macOS, if `docker` is not found but Docker
Desktop is installed in Applications, add its bundled CLI to the current terminal's path:

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
```

```bash
cp .env.example .env
docker compose up -d --wait postgres
docker compose build pipeline
docker compose run --rm pipeline migrate
docker compose run --rm pipeline generate
docker compose run --rm pipeline run --mode full --page-size 2
docker compose run --rm pipeline health
docker compose exec -T postgres psql -U pipeline -d pipeline -c 'TABLE reporting.open_pipeline'
```

Initial source: 2 accounts, 4 opportunities, 4 activities, 6 explicit stage events (16 envelopes).
Open value: $3,000.00; win rate: 0.5; average cycle for closed deals: 15 days.

The example credentials are for local synthetic development. `.env` and generated `data/` are
ignored. Compose reads `.env` for substitution and supplies container URLs itself. The host URL in
`.env.example` is for host development; change the port there as well if changing `POSTGRES_PORT`.
After a password change, an existing PostgreSQL volume still has its original database password.

## Incremental updates, replay, and backfill

```bash
# Revision 1 preserves the original journal prefix and appends four deliveries:
# one opportunity change, its duplicate, an old meeting arriving late, and an explicit stage event.
docker compose run --rm pipeline generate --revision 1
docker compose run --rm pipeline run --page-size 2
docker compose run --rm pipeline run             # No new deliveries: safe no-op ingestion
docker compose run --rm pipeline run --mode full # Replay all deliveries; no duplicate entities

# Half-open UTC updated_at range; does not move the normal checkpoint or create past snapshots.
docker compose run --rm pipeline run --mode backfill \
  --start 2026-10-01T00:00:00Z --end 2026-11-01T00:00:00Z
```

Use a full load before narrow backfills so referenced parents are present. Full means replay/upsert,
not truncate or delete. Do not regenerate revision 0 after consuming revision 1: journal truncation
is rejected. Correct source records by appending a new version with a strictly newer `updated_at`.
Arrival sequence, not a global event-time watermark, is the incremental cursor.

## Scheduling and health

```bash
docker compose run --rm pipeline schedule --interval-seconds 300
# Bounded scheduler smoke check:
docker compose run --rm pipeline schedule --interval-seconds 1 --iterations 2
docker compose run --rm pipeline health --max-age-seconds 900
docker compose exec -T postgres psql -U pipeline -d pipeline -c 'TABLE reporting.pipeline_health'
```

The foreground scheduler runs an incremental load immediately, waits after each run, and handles
SIGINT/SIGTERM. Run it under a local process supervisor if continuous operation is needed. PostgreSQL
advisory locks reject conflicting runs. The scheduler continues after an error and logs sanitized
JSON; inspect health to alert on failures. It does not generate source changes itself.

Health reports the latest attempt, duration, counts, rejected rows, attempts, checkpoint,
publication age, source-event age, and last successful run. A fresh successful publication with
no unresolved rejects is healthy. Source-event age is separate: fixed synthetic dates remain old
even when scheduling works. CLI exit codes: **0** success/healthy, **2** partial/unhealthy,
**1** failure (argument parsing also uses argparse's exit code 2).

## Quarantine and recovery

```bash
docker compose exec -T postgres psql -U pipeline -d pipeline \
  -c 'SELECT sequence,reason,acknowledged_at FROM pipeline_ops.quarantine ORDER BY sequence'
```

A partial run saves raw records, valid staging updates, and field/error reasons for rejects. It
keeps the checkpoint and last complete reporting publication. Missing-parent errors can recover
automatically when parents arrive on a later run. For an invalid immutable delivery, append its
corrected replacement, review the rejected raw record, and explicitly acknowledge the old delivery:

```bash
# Replace 21 with a reviewed quarantined sequence; this records an audit note and explicit data loss decision.
docker compose run --rm pipeline acknowledge 21 --note 'Reviewed synthetic invalid delivery; corrected version appended'
docker compose run --rm pipeline run
```

Acknowledging alone does not publish or advance a checkpoint. The next successful run does so.
Acknowledged rejections remain recorded and are skipped on replays. Never edit an old journal
envelope in place. Failure injection (`--fail-at after_staging` or `after_publish`) rolls back raw,
staging, reports, snapshots, and checkpoint together. Run status remains separately visible.
If a process or database connection dies, the unfinished run is marked interrupted by the next
lock holder; an unavailable database cannot receive status writes until it recovers.

## Reproducible demo

```bash
docker compose run --rm pipeline demo
```

This requires local database-creation permission (the supplied development user has it). Every
demo creates a new `pipeline_demo_<suffix>` database and retains it for inspection. It never resets
an existing database. The demo asserts:

| Step | Measured fixture result |
| --- | --- |
| Full load | Checkpoint 16; $3,000.00 open value |
| Append revision 1; inject failure after reporting SQL | Checkpoint still 16; $3,000.00 still published |
| Retry normally | Checkpoint 20; $3,500.00 open value; 3 distinct entity changes from 4 deliveries |

## Tests

Use a dedicated test database: tests truncate **pipeline-owned tables** there before each case.
The URL must end in `_test`; never use an application database. Run sequentially, without pytest-xdist.

```bash
# Once per local database volume (skip if already created):
docker compose exec -T postgres createdb -U pipeline pipeline_test
docker compose run --rm --entrypoint pytest \
  -e TEST_DATABASE_URL=postgresql://pipeline:pipeline_local@postgres:5432/pipeline_test pipeline -q
docker compose run --rm --entrypoint ruff pipeline check src tests scripts
docker compose run --rm --entrypoint ruff pipeline format --check src tests scripts
```

If you changed the development password, substitute it in the test URL using your local environment;
do not commit it. Database tests skip without `TEST_DATABASE_URL`; source/validation tests still run.
CI always provides PostgreSQL. Tests cover known metric results, pagination, duplicate deliveries,
late events and updates, stale versions, invalid fields and relationships, full replays,
partial publication, retries, locking, source mutation, interrupted runs, backfills, and checkpoints
after injected failures. [Backend verification commands](docs/integration.md) test the real API.

## Host development

Python 3.12–3.14 and PostgreSQL 17 are supported; the backend uses Python 3.14.

```bash
uv sync --frozen --extra dev
export DATABASE_URL=postgresql://pipeline:pipeline_local@localhost:55432/pipeline
uv run sales-pipeline migrate
uv run sales-pipeline generate
uv run sales-pipeline run --mode full
```

Host commands read `DATABASE_URL` and `SOURCE_PATH` from the shell; they do not load `.env` implicitly.
After source changes, rebuild the image. SQL migrations ship inside the Python package and are
transactional and checksum-verified. Add a numbered migration; never edit an applied one.

## Current scope

One append-only source per database; single tenant, matching the existing backend. The journal and
selected batch are held in memory and reports are rebuilt transactionally: appropriate for a local
engineering demo, with no benchmark or production-scale claim. No source deletes, currency conversion,
PII ingestion, retention policy, webhook/API connector, or multi-tenant deployment is implemented.
The backend endpoints expose synthetic warehouse reports; existing CRM CRUD/analytics retain their
own data. [Integration guidance](docs/integration.md) explains how to share a database and what a real
source adapter must guarantee.
