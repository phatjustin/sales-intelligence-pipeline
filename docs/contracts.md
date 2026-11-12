# Source and storage contracts

## Journal v1

UTF-8 JSON Lines. The first line is a manifest:

```json
{"format":"synthetic-crm-journal-v1","source_id":"synthetic-crm-demo-v1"}
```

Each subsequent envelope has `sequence` (positive contiguous integer, arrival order), `entity`
(`accounts`, `opportunities`, `activities`, `stage_changes`), and `record` (object). Exact duplicate
envelopes may be retransmitted. A sequence cannot be reassigned to changed data. The source ID is
bound to a database on first load; distinct sources need distinct databases until explicit source
namespacing is added. Source adapters must preserve the entire journal in this implementation.

All records require `id` (UUID), `created_at`, and `updated_at` (ISO 8601 timestamps with UTC offset).
Update time must be at least creation time. Extra fields are rejected; this minimizes unnecessary
CRM and personal fields in the pipeline. Entity-specific fields:

| Entity | Additional fields |
| --- | --- |
| accounts | `name` (1–200 characters) |
| opportunities | `account_id`, `name`, `stage`, `value` (nonnegative decimal USD, at most 12 integer and 2 fractional digits), optional `closed_at` |
| activities | `opportunity_id`, `kind` (`email`, `meeting`, `crm_event`), `occurred_at` |
| stage_changes | `opportunity_id`, optional `from_stage`, required `to_stage`, `changed_at` |

Won/lost require `closed_at`; open stages require null/absent `closed_at`. Closed timestamp must
lie between creation and update. Activity/transition times must not precede opportunity creation
or exceed the event record's update time. `from_stage` and `to_stage` must differ. Six valid stages
match the backend exactly. UUIDs use stable source identities, not names, for upserts. Existing
creation times and relationship IDs are immutable; corrections to other fields require a newer
per-record update timestamp. Equal timestamps with different canonical content are quarantined.

Example opportunity envelope:

```json
{"sequence":3,"entity":"opportunities","record":{"id":"c106cb32-f4aa-5c8b-bb6b-000000000001","account_id":"c106cb32-f4aa-5c8b-bb6b-000000000002","name":"Synthetic opportunity","stage":"proposal","value":"1000.00","created_at":"2026-10-01T00:00:00Z","updated_at":"2026-10-05T00:00:00Z","closed_at":null}}
```

The example IDs are illustrative; the account must exist before the opportunity can be accepted.
Ingestion processes accounts before opportunities before activities/history, across page boundaries.
Records referencing parents outside a backfill range need those parents already ingested.

## Schema ownership

| Schema | Objects / keys |
| --- | --- |
| `pipeline_ops` | Checksummed migration ledger; singleton source checkpoint; UUID run records with counts and UTC bounds; quarantine keyed by source sequence with reason and optional acknowledgement audit note |
| `raw` | One immutable JSONB record per arrival sequence, fingerprint, entity name, first-run ID, database receipt timestamp |
| `staging` | Valid current accounts/opportunities/activities/stage changes, UUID keys, foreign keys, typed values and timestamps, canonical record fingerprints |
| `reporting` | SQL metric tables, explicit stage intervals, observed weekly snapshots, singleton publication metadata, health view |

`raw.records` contains each delivery's original record object, including invalid objects. Different
versions of the same source ID remain available there. Staging contains only the latest accepted
version. Acknowledged invalid deliveries remain raw/quarantined and are excluded from staging.

`rows_read` counts selected unique delivery sequences for that attempt. `rows_applied` counts
staging inserts/newer updates committed by a successful or partial run; duplicate/stale/acknowledged
rows are not applied. On failure `rows_applied=0` because staging changes rolled back; `rows_read`
and rejected counts describe work observed before rollback, not retained rejected records.
Pagination is extraction batching; publication remains one atomic transaction.

## SQL examples

```sql
SELECT stage, opportunity_count, value FROM reporting.open_pipeline ORDER BY stage;
SELECT won_count,lost_count,win_rate,average_sales_cycle_days FROM reporting.closed_performance;
SELECT account_id,activity_count,contact_count,last_contact_at
FROM reporting.account_activity ORDER BY account_id LIMIT 50;
SELECT opportunity_id,to_stage,entered_at,exited_at,duration_days,chain_valid
FROM reporting.stage_history ORDER BY opportunity_id,entered_at;
SELECT week_start,observed_at,stage,value FROM reporting.weekly_snapshots ORDER BY week_start,stage;
SELECT * FROM reporting.pipeline_health;
```

For several queries that must agree, use `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;`
and `COMMIT;`. Application accounts should receive only `USAGE` on `reporting` and `SELECT` on its
tables/views for warehouse reads. Raw/staging/operations access is for the pipeline operator.
