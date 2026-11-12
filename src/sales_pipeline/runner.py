import json
import time
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from sales_pipeline.db import LOCK_ID, connect
from sales_pipeline.models import MODELS
from sales_pipeline.source import Journal, fingerprint
from sales_pipeline.transform import publish


class ConcurrentRun(RuntimeError):
    pass


class InjectedFailure(RuntimeError):
    pass


def apply_record(conn, entity, payload):
    if entity not in MODELS:
        return "unknown_entity", False
    try:
        record = MODELS[entity].model_validate(payload)
    except ValidationError as exc:
        # Field paths and error codes only: never echo submitted values.
        reason = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'record'}:{e['type']}"
            for e in exc.errors(include_input=False)
        )
        return reason, False
    values = record.model_dump()
    digest = fingerprint(record.model_dump(mode="json"))
    table = sql.Identifier("staging", entity)
    existing = conn.execute(
        sql.SQL("SELECT * FROM {} WHERE id=%s").format(table), (record.id,)
    ).fetchone()
    if existing:
        if record.updated_at < existing["updated_at"]:
            return None, False
        if record.updated_at == existing["updated_at"]:
            return (
                None if digest == existing["fingerprint"] else "conflicting_same_version"
            ), False
        if record.created_at != existing["created_at"]:
            return "created_at_is_immutable", False
    if (
        entity == "opportunities"
        and not conn.execute(
            "SELECT 1 FROM staging.accounts WHERE id=%s",
            (record.account_id,),
        ).fetchone()
    ):
        return "account_id:missing_parent", False
    if entity in {"activities", "stage_changes"}:
        parent = conn.execute(
            "SELECT created_at FROM staging.opportunities WHERE id=%s", (record.opportunity_id,)
        ).fetchone()
        if parent is None:
            return "opportunity_id:missing_parent", False
        event_at = values.get("occurred_at", values.get("changed_at"))
        if event_at < parent["created_at"]:
            return "event_before_opportunity_created", False
        if existing and record.opportunity_id != existing["opportunity_id"]:
            return "opportunity_id_is_immutable", False
    if entity == "opportunities" and existing and record.account_id != existing["account_id"]:
        return "account_id_is_immutable", False
    if (
        entity == "stage_changes"
        and conn.execute(
            "SELECT 1 FROM staging.stage_changes "
            "WHERE opportunity_id=%s AND changed_at=%s AND id<>%s",
            (record.opportunity_id, record.changed_at, record.id),
        ).fetchone()
    ):
        return "ambiguous_stage_event_time", False
    values["fingerprint"] = digest
    columns = list(values)
    conn.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT(id) DO UPDATE SET {}").format(
            table,
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(sql.Placeholder() for _ in columns),
            sql.SQL(",").join(
                sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
                for c in columns
                if c != "id"
            ),
        ),
        tuple(values.values()),
    )
    return None, True


def run(
    url,
    source,
    *,
    mode="incremental",
    start=None,
    end=None,
    page_size=100,
    fail_at=None,
    max_attempts=3,
):
    if mode not in {"full", "incremental", "backfill"}:
        raise ValueError("invalid_mode")
    if page_size < 1 or max_attempts < 1 or max_attempts > 5:
        raise ValueError("invalid_run_bounds")
    if mode == "backfill" and (start is None or end is None or end <= start):
        raise ValueError("ordered_backfill_bounds_required")
    if mode != "backfill" and (start is not None or end is not None):
        raise ValueError("range_only_for_backfill")
    run_id = uuid4()
    started = time.monotonic()
    with connect(url) as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (LOCK_ID,)).fetchone()[
            "acquired"
        ]:
            raise ConcurrentRun("another_pipeline_or_migration_is_running")
        # Holding the global lock proves earlier running rows have no active owner.
        conn.execute("""UPDATE pipeline_ops.runs SET status='failed', finished_at=clock_timestamp(),
            error_code='interrupted' WHERE status='running'""")
        conn.execute(
            "INSERT INTO pipeline_ops.checkpoint(singleton,source_id) VALUES (true,%s) "
            "ON CONFLICT DO NOTHING",
            (source.source_id,),
        )
        checkpoint = conn.execute("SELECT * FROM pipeline_ops.checkpoint").fetchone()
        before = checkpoint["sequence"]
        conn.execute(
            """INSERT INTO pipeline_ops.runs
            (id,source_id,mode,status,checkpoint_before,checkpoint_after,range_start,range_end)
            VALUES (%s,%s,%s,'running',%s,%s,%s,%s)""",
            (run_id, source.source_id, mode, before, before, start, end),
        )
        for attempt in range(1, max_attempts + 1):
            read = applied = rejected = 0
            try:
                with conn.transaction():
                    if checkpoint["source_id"] != source.source_id:
                        raise ValueError("database_bound_to_other_source")
                    if source.head < before:
                        raise ValueError("source_truncated_before_checkpoint")
                    # Validate immutable acknowledged prefix, including zero-row incremental runs.
                    known = conn.execute("SELECT sequence,fingerprint FROM raw.records").fetchall()
                    for row in known:
                        if row["sequence"] > source.head:
                            raise ValueError("source_truncated_before_raw_history")
                        if fingerprint(source.events[row["sequence"] - 1]) != row["fingerprint"]:
                            raise ValueError("source_journal_mutated")
                    pending = []
                    for page in source.pages(
                        after=before if mode == "incremental" else 0,
                        start=start,
                        end=end,
                        page_size=page_size,
                    ):
                        for event in page:
                            read += 1
                            seq = event["sequence"]
                            digest = fingerprint(event)
                            previous = conn.execute(
                                "SELECT fingerprint FROM raw.records WHERE sequence=%s", (seq,)
                            ).fetchone()
                            if previous and previous["fingerprint"] != digest:
                                raise ValueError("source_journal_mutated")
                            conn.execute(
                                """INSERT INTO raw.records
                                (sequence,entity,payload,fingerprint,first_run_id)
                                VALUES (%s,%s,%s,%s,%s)
                                ON CONFLICT DO NOTHING""",
                                (seq, event["entity"], Jsonb(event["record"]), digest, run_id),
                            )
                            pending.append(event)
                    # Parents precede children even if source pages arrive out of entity order.
                    order = {entity: i for i, entity in enumerate(MODELS)}
                    pending.sort(key=lambda e: (order.get(e["entity"], 99), e["sequence"]))
                    for event in pending:
                        seq = event["sequence"]
                        acknowledged = conn.execute(
                            "SELECT acknowledged_at FROM pipeline_ops.quarantine WHERE sequence=%s",
                            (seq,),
                        ).fetchone()
                        if acknowledged and acknowledged["acknowledged_at"]:
                            continue
                        reason, changed = apply_record(conn, event["entity"], event["record"])
                        if reason:
                            rejected += 1
                            conn.execute(
                                """INSERT INTO pipeline_ops.quarantine(sequence,reason,last_run_id)
                                VALUES (%s,%s,%s) ON CONFLICT(sequence) DO UPDATE
                                SET reason=EXCLUDED.reason,last_run_id=EXCLUDED.last_run_id""",
                                (seq, reason, run_id),
                            )
                        else:
                            applied += int(changed)
                            conn.execute(
                                "DELETE FROM pipeline_ops.quarantine WHERE sequence=%s", (seq,)
                            )
                    if fail_at == "after_staging":
                        raise InjectedFailure("injected_after_staging")
                    # Partial runs retain raw/quarantine/staging and the last complete report.
                    after = before
                    unresolved = conn.execute(
                        "SELECT count(*) AS n FROM pipeline_ops.quarantine "
                        "WHERE acknowledged_at IS NULL"
                    ).fetchone()["n"]
                    if not unresolved:
                        publish(
                            conn,
                            run_id,
                            before if mode == "backfill" else source.head,
                            snapshot=mode != "backfill",
                        )
                        if fail_at == "after_publish":
                            raise InjectedFailure("injected_after_publish")
                        if mode != "backfill":
                            after = max(before, source.head)
                            conn.execute("UPDATE pipeline_ops.checkpoint SET sequence=%s", (after,))
                    status = "partial" if unresolved else "succeeded"
                    conn.execute(
                        """UPDATE pipeline_ops.runs SET status=%s,finished_at=clock_timestamp(),
                        rows_read=%s,rows_applied=%s,rejected_rows=%s,checkpoint_after=%s,attempts=%s
                        WHERE id=%s""",
                        (status, read, applied, rejected, after, attempt, run_id),
                    )
                result = dict(
                    run_id=str(run_id),
                    status=status,
                    rows_read=read,
                    rows_applied=applied,
                    rejected_rows=rejected,
                    checkpoint_before=before,
                    checkpoint_after=after,
                    attempts=attempt,
                    duration_ms=round((time.monotonic() - started) * 1000, 2),
                )
                print(json.dumps(dict(event="pipeline_run", **result)), flush=True)
                return result
            except Exception as exc:
                transient = isinstance(
                    exc, (psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected)
                )
                if transient and attempt < max_attempts:
                    time.sleep(0.1 * 2 ** (attempt - 1))
                    continue
                # Error class only: database errors can contain row data or connection secrets.
                conn.execute(
                    """UPDATE pipeline_ops.runs SET status='failed', finished_at=clock_timestamp(),
                    attempts=%s,error_code=%s,rows_read=%s,rejected_rows=%s WHERE id=%s""",
                    (attempt, type(exc).__name__, read, rejected, run_id),
                )
                print(
                    json.dumps(
                        dict(
                            event="pipeline_run",
                            run_id=str(run_id),
                            status="failed",
                            error_code=type(exc).__name__,
                            attempts=attempt,
                        )
                    ),
                    flush=True,
                )
                raise


def run_path(url, path, **kwargs):
    """Source failures are visible as failed runs, without printing file contents or paths."""
    try:
        for attempt in range(3):
            try:
                source = Journal(path)
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.1 * 2**attempt)
    except Exception as exc:
        with connect(url) as conn:
            if not conn.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired", (LOCK_ID,)
            ).fetchone()["acquired"]:
                raise ConcurrentRun("another_pipeline_or_migration_is_running") from None
            checkpoint = conn.execute("SELECT * FROM pipeline_ops.checkpoint").fetchone()
            before = checkpoint["sequence"] if checkpoint else 0
            conn.execute(
                """INSERT INTO pipeline_ops.runs
                (id,source_id,mode,status,finished_at,checkpoint_before,checkpoint_after,error_code)
                VALUES (%s,%s,%s,'failed',clock_timestamp(),%s,%s,%s)""",
                (
                    uuid4(),
                    checkpoint["source_id"] if checkpoint else "unavailable",
                    kwargs.get("mode", "incremental"),
                    before,
                    before,
                    type(exc).__name__,
                ),
            )
        raise
    return run(url, source, **kwargs)


def acknowledge(url, sequence, note):
    if not note.strip() or len(note) > 200:
        raise ValueError("acknowledgement_note_required_max_200")
    with connect(url) as conn:
        with conn.transaction():
            acquired = conn.execute(
                "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (LOCK_ID,)
            ).fetchone()["acquired"]
            if not acquired:
                raise ConcurrentRun("another_pipeline_or_migration_is_running")
            result = conn.execute(
                """UPDATE pipeline_ops.quarantine
                SET acknowledged_at=clock_timestamp(),acknowledgement=%s
                WHERE sequence=%s AND acknowledged_at IS NULL""",
                (note, sequence),
            )
            if not result.rowcount:
                raise ValueError("unresolved_quarantine_record_not_found")
