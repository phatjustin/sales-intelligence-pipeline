# Existing Python application integration

The backend is available at `../python-sales-intelligence`. Its `api/app.py` now mounts
`api/warehouse.py` under the same `Depends(authenticate)` dependency used by CRM/jobs routes.
The router uses the established `Session` alias and SQLAlchemy `text()` queries. No new backend
dependency, authentication method, frontend change, CRM table mutation, or tenant parameter is
introduced. Existing application data and its analytics retain their existing behavior.

The source-controlled adapter is [warehouse.py](../integrations/backend/warehouse.py). For a
fresh sibling checkout with matching conventions:

```bash
python3 scripts/install_backend.py ../python-sales-intelligence
```

The installer checks its anchors and refuses to overwrite a differing router. Review an existing
router before updating it. Rebuild/restart the backend after installing. Its Alembic migrations
still own only its application schema; the pipeline migration ledger is independent.

## Share the backend's database

The backend's SQLAlchemy URL uses `postgresql+asyncpg://`; the pipeline uses a psycopg/libpq URL,
`postgresql://`, for the **same host, port, database, and appropriate credentials**. Configure that
pipeline URL in the shell and run the pipeline migrations and ingestion there:

```bash
# Example matches the backend's documented local defaults, using only synthetic development data.
export DATABASE_URL=postgresql://sales:sales@localhost:5432/sales
uv run sales-pipeline migrate
uv run sales-pipeline generate
uv run sales-pipeline run --mode full
```

Alternatively configure the backend's `DATABASE_URL` to the standalone pipeline database after
applying its own Alembic migrations there. Do not switch a running application to an unmigrated
database. The default Compose setup in this project deliberately creates an isolated database;
it does not alter an existing running application's database or configuration automatically.

Both backend and pipeline schemas can coexist: application tables are in `public`; ingestion
owns `raw`, `staging`, `reporting`, and `pipeline_ops`. Use a pipeline migration/writer role and
grant the API role only read access to warehouse reports:

```sql
-- Substitute the existing application's role name as appropriate.
GRANT USAGE ON SCHEMA reporting TO sales;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO sales;
-- Run as the role that owns pipeline migrations, for future reporting objects:
ALTER DEFAULT PRIVILEGES IN SCHEMA reporting GRANT SELECT ON TABLES TO sales;
```

Local Compose uses one development database owner for simplicity; that example is not a
least-privilege deployment. The API router executes only SELECT statements, and exposes no raw
records or quarantine payloads. A reader role cannot perform a full CRM role's writes; grant
warehouse SELECT privileges to the existing CRM role if sharing a session as implemented.

## Read-only routes

| GET path | Response |
| --- | --- |
| `/api/v1/warehouse/metrics` | Complete publication timestamp/run ID, currency, open value/stages, closed-performance metrics |
| `/api/v1/warehouse/status` | Latest attempt, publication age, source-event age, counts, checkpoint, and 900-second health threshold |
| `/api/v1/warehouse/accounts?limit=50&offset=0` | `{items,total,limit,offset}`; counts and last-contact timestamps, ordered by account UUID |

Metrics use one SQL statement for a consistent PostgreSQL snapshot. They return 503 if reports are
missing or unpublished. Decimal money and closed metrics serialize as strings, consistent with the
backend. Status exposes both latest attempt and last complete publication; use both when a run is
partial or failed. Account metrics can be empty before the first publication; status and metrics
distinguish that state.

```bash
curl -H 'X-API-Key: YOUR_LOCAL_KEY' http://localhost:8000/api/v1/warehouse/metrics
curl -H 'X-API-Key: YOUR_LOCAL_KEY' http://localhost:8000/api/v1/warehouse/status
```

The backend is explicitly single-tenant. These routes preserve that boundary. Do not mount them
into a multi-tenant deployment without source/tenant keys, enforced filters/RLS, and tenant tests
across every raw, staging, reporting, and operational relation. The current synthetic account IDs
belong to this source, not the backend's separate seeder. No join or identity equivalence with
application-seeded accounts is implied. A future shared source must supply stable account IDs.

## Verify the actual backend locally

After the pipeline tests pass against `pipeline_test`, prepare its known fixture:

```bash
docker compose run --rm --entrypoint python \
  -e TEST_DATABASE_URL=postgresql://pipeline:pipeline_local@postgres:5432/pipeline_test \
  pipeline scripts/prepare_backend_test.py
docker compose -f compose.yaml -f compose.integration.yaml build backend-test
docker compose -f compose.yaml -f compose.integration.yaml run --rm backend-test
```

The backend test image uses the backend's existing frozen lockfile, imports the real application,
and exercises its original authentication dependency and PostgreSQL reporting queries through
ASGI HTTP requests. It needs neither Redis nor AI for these routes. Its dedicated tests assert
$3,500 open value, 0.5 win rate, API-key denial, pagination, method restrictions, and sanitized 503s.
They do not require or modify the frontend. Run these separately from destructive pipeline test
fixtures so the database is not reset concurrently.

## Future source integration

The actual CRM application is not currently an ingestion source. Its list API has offset pagination,
without a durable change cursor/deletion feed or explicit stage-history endpoint. Polling it as if
it had those guarantees would miss deleted records and fabricate completeness. A live adapter
needs a consistent paginated extract, stable IDs, durable arrival/change cursor, deletion semantics,
and explicit stage/close events. Its JSON audit events may be mapped only when their meaning is
known and tested; current stage values alone are insufficient. Until that is implemented, the
synthetic journal is the complete local alternative and these warehouse routes are its application
integration point.
