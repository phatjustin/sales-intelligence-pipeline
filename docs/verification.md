# Local verification record

Verified on **2026-11-12** using Docker, Python 3.14.0, PostgreSQL 17.6, and the frozen uv lockfiles.
These are local synthetic correctness checks. No production performance or business-impact
measurement was performed.

| Check | Observed result |
| --- | --- |
| First implemented slice | 6 account/opportunity source records; PostgreSQL full load succeeded; repeat incremental read 0; open metric $3,000.00 |
| Final pipeline suite | **22 passed**: 17 real PostgreSQL cases plus 5 source/validation cases |
| New backend integration suite | **2 passed**, importing the real FastAPI application and original authentication dependency against populated PostgreSQL reports |
| Existing backend regression suite | **36 passed, 3 deselected**; original non-integration tests using their expected no-key configuration |
| Static checks | Pipeline/adapter Ruff lint and formatting passed; backend's own lint rules passed for the modified application/router files |
| Scheduler smoke | Two bounded iterations succeeded, each with 0 new rows; checkpoint remained 16 |
| Health CLI | `healthy=true`, latest run `succeeded`, 0 unresolved rejects, source sequence 16 |
| Frozen Docker build | Succeeded with `uv sync --frozen --extra dev` |

## Recovery demo evidence

`docker compose run --rm pipeline demo` created and retained the isolated database
`pipeline_demo_1879a5ef`. It produced three run records:

1. Initial full run: 16 deliveries applied, checkpoint 0 → 16, $3,000.00 open value.
2. Revision 1 with injected failure **after reporting SQL**: failed run; checkpoint remained 16,
   prior $3,000.00 report remained published. The changed data and reporting transaction rolled back.
3. Normal retry: 4 deliveries read, 3 distinct entity changes applied, checkpoint 16 → 20,
   $3,500.00 open value. Duplicate delivery and late-arriving meeting were handled.

The demo asserts these amounts and checkpoints in code. Separate tests also inject failure after
staging, exhaust transient retries, verify no rows/cursor leak on rollback, and recover an interrupted
run. Durations emitted by the local logs describe those individual runs only; they are not benchmarks.

The known fixture additionally verifies a win rate of 0.5, mean closed cycle of 15 calendar days,
explicit stage durations of 10/20 days, contact dates excluding CRM audit events, and preservation
of the first $3,000 weekly observation after current value changes to $3,500.

## Scope of application verification

The sibling backend contains two scoped code changes: a new read-only warehouse router and its
authenticated router mount. Its existing API routes passed regression tests. The frontend was
inspected and needed no changes. The backend integration uses its actual application/session and
SQL queries through ASGI requests, rather than a mocked report service. Its original Redis-dependent
integration tests were excluded because they are outside the pipeline verification scope.

The running application's environment/database was not reconfigured. To expose warehouse data from
that application instance, use [the shared-database setup](integration.md) and restart its backend.
The standalone pipeline database, test database, and retained demo database are separate. CI is
configured for PostgreSQL tests; no remote CI execution is claimed here.
