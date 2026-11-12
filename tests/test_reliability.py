import json
from decimal import Decimal

import psycopg
import pytest

from sales_pipeline.db import LOCK_ID, connect, migrate
from sales_pipeline.health import health
from sales_pipeline.models import timestamp
from sales_pipeline.runner import ConcurrentRun, InjectedFailure, acknowledge, run_path
from sales_pipeline.source import Journal
from sales_pipeline.synthetic import generate, uid

pytestmark = pytest.mark.integration


def source(tmp_path, revision=0):
    path = tmp_path / "crm.jsonl"
    generate(path, revision)
    return path


def scalar(url, query):
    with connect(url) as conn:
        return next(iter(conn.execute(query).fetchone().values()))


def append(path, entity, record):
    seq = Journal(path).head + 1
    with path.open("a") as stream:
        stream.write(json.dumps(dict(sequence=seq, entity=entity, record=record)) + "\n")
    return seq


def test_known_metrics_and_explicit_history(db, tmp_path):
    path = source(tmp_path)
    run_path(db, path, mode="full", page_size=2)
    with connect(db) as conn:
        metric = conn.execute("SELECT * FROM reporting.closed_performance").fetchone()
        assert (metric["won_count"], metric["lost_count"]) == (1, 1)
        assert metric["win_rate"] == Decimal("0.5")
        assert metric["average_sales_cycle_days"] == Decimal("15")
        activity = conn.execute(
            "SELECT * FROM reporting.account_activity WHERE account_id=%s", (uid("a1"),)
        ).fetchone()
        assert (activity["activity_count"], activity["contact_count"]) == (2, 1)
        assert activity["last_contact_at"] == timestamp("2026-10-03T00:00:00Z")
        intervals = conn.execute(
            "SELECT duration_days FROM reporting.stage_history "
            "WHERE to_stage='prospecting' AND exited_at IS NOT NULL "
            "ORDER BY duration_days"
        ).fetchall()
        assert [row["duration_days"] for row in intervals] == [10, 20]
        assert (
            conn.execute("SELECT count(*) AS n FROM reporting.weekly_snapshots").fetchone()["n"]
            == 4
        )


def test_incremental_duplicates_and_arbitrarily_late_updates(db, tmp_path):
    path = source(tmp_path)
    run_path(db, path, mode="full")
    generate(path, 1)
    result = run_path(db, path, page_size=1)
    assert (result["rows_read"], result["rows_applied"], result["checkpoint_after"]) == (4, 3, 20)
    assert scalar(db, "SELECT sum(value) FROM reporting.open_pipeline") == 3500
    assert scalar(db, "SELECT count(*) FROM staging.activities") == 5
    assert scalar(db, "SELECT count(*) FROM staging.opportunities") == 4
    assert scalar(db, "SELECT sum(value) FROM reporting.weekly_snapshots") == 3000
    run_path(db, path, mode="full")
    assert scalar(db, "SELECT count(*) FROM raw.records") == 20
    assert scalar(db, "SELECT count(*) FROM reporting.stage_history") == 7
    # A stale version delivered later cannot overwrite current state.
    record = Journal(path).events[2]["record"]
    append(path, "opportunities", record)
    assert run_path(db, path)["rows_applied"] == 0
    assert scalar(db, "SELECT sum(value) FROM reporting.open_pipeline") == 3500


def test_late_opportunity_correction_uses_arrival_cursor(db, tmp_path):
    path = source(tmp_path, 1)
    run_path(db, path, mode="full")
    record = dict(Journal(path).events[3]["record"])
    # This update is older than the global November watermark, newer for this opportunity.
    record.update(value="2250.00", updated_at="2026-10-15T00:00:00Z")
    append(path, "opportunities", record)
    assert run_path(db, path)["rows_applied"] == 1
    assert scalar(db, "SELECT sum(value) FROM reporting.open_pipeline") == 3750


def test_missing_parent_recovers_without_skipping_child(db, tmp_path):
    path = source(tmp_path)
    original = dict(Journal(path).events[2]["record"])
    original.update(id=uid("new-child"), account_id=uid("arrives-later"))
    append(path, "opportunities", original)
    assert run_path(db, path)["status"] == "partial"
    assert scalar(db, "SELECT sequence FROM pipeline_ops.checkpoint") == 0
    parent = dict(Journal(path).events[0]["record"], id=uid("arrives-later"))
    append(path, "accounts", parent)
    assert run_path(db, path)["checkpoint_after"] == 18
    assert scalar(db, "SELECT count(*) FROM staging.opportunities") == 5


def test_current_state_does_not_generate_stage_history(db, tmp_path):
    path = source(tmp_path)
    path.write_text("\n".join(path.read_text().splitlines()[:11]) + "\n")
    run_path(db, path)
    assert scalar(db, "SELECT count(*) FROM reporting.stage_history") == 0
    assert scalar(db, "SELECT sum(value) FROM reporting.open_pipeline") == 3000


@pytest.mark.parametrize("failure", ["after_staging", "after_publish"])
def test_failed_run_rolls_back_data_reports_and_checkpoint(db, tmp_path, failure):
    path = source(tmp_path)
    run_path(db, path, mode="full")
    old_run = scalar(db, "SELECT run_id FROM reporting.publication")
    generate(path, 1)
    with pytest.raises(InjectedFailure):
        run_path(db, path, fail_at=failure)
    assert scalar(db, "SELECT sequence FROM pipeline_ops.checkpoint") == 16
    assert scalar(db, "SELECT count(*) FROM raw.records") == 16
    assert scalar(db, "SELECT run_id FROM reporting.publication") == old_run
    assert scalar(db, "SELECT sum(value) FROM reporting.open_pipeline") == 3000
    assert not health(db)["healthy"]
    assert run_path(db, path)["checkpoint_after"] == 20
    assert health(db)["healthy"]


def test_invalid_records_partial_publication_and_acknowledgement(db, tmp_path):
    path = source(tmp_path)
    run_path(db, path, mode="full")
    generate(path, 1)
    invalid = dict(Journal(path).events[2]["record"], id=uid("invalid"), stage="not-a-stage")
    seq = append(path, "opportunities", invalid)
    result = run_path(db, path)
    assert (result["status"], result["rejected_rows"], result["checkpoint_after"]) == (
        "partial",
        1,
        16,
    )
    assert scalar(db, "SELECT sum(value) FROM reporting.open_pipeline") == 3000
    assert scalar(db, "SELECT count(*) FROM pipeline_ops.quarantine") == 1
    assert run_path(db, path)["status"] == "partial"
    # Even an unrelated backfill must not publish staging containing unresolved rejects.
    assert (
        run_path(
            db,
            path,
            mode="backfill",
            start=timestamp("2026-10-03T00:00:00Z"),
            end=timestamp("2026-10-04T00:00:00Z"),
        )["status"]
        == "partial"
    )
    acknowledge(db, seq, "Synthetic invalid fixture reviewed; skip this delivery")
    assert run_path(db, path)["checkpoint_after"] == seq
    assert scalar(db, "SELECT sum(value) FROM reporting.open_pipeline") == 3500


def test_relationship_and_version_conflicts_quarantined(db, tmp_path):
    path = source(tmp_path)
    run_path(db, path, mode="full")
    record = dict(Journal(path).events[2]["record"])
    append(path, "opportunities", {**record, "value": "999.00"})
    append(path, "opportunities", {**record, "id": uid("orphan"), "account_id": uid("missing")})
    append(path, "opportunities", {**record, "id": uid("missing-field"), "name": None})
    append(path, "opportunities", {**record, "id": uid("bad-money"), "value": "1.001"})
    result = run_path(db, path)
    assert result["rejected_rows"] == 4
    assert scalar(db, "SELECT sequence FROM pipeline_ops.checkpoint") == 16
    with connect(db) as conn:
        reasons = [
            row["reason"] for row in conn.execute("SELECT reason FROM pipeline_ops.quarantine")
        ]
    assert "account_id:missing_parent" in reasons
    assert "conflicting_same_version" in reasons


def test_backfill_does_not_move_cursor_or_invent_snapshots(db, tmp_path):
    path = source(tmp_path)
    run_path(db, path, mode="full")
    generate(path, 1)
    result = run_path(
        db,
        path,
        mode="backfill",
        start=timestamp("2026-11-01T00:00:00Z"),
        end=timestamp("2026-11-03T00:00:00Z"),
    )
    assert (result["rows_read"], result["checkpoint_after"]) == (3, 16)
    assert scalar(db, "SELECT source_sequence FROM reporting.publication") == 16
    assert scalar(db, "SELECT sum(value) FROM reporting.weekly_snapshots") == 3000
    result = run_path(db, path)
    assert result["checkpoint_after"] == 20
    assert scalar(db, "SELECT count(*) FROM staging.activities") == 5


def test_reject_conflicting_run_and_recover_interrupted_run(db, tmp_path):
    path = source(tmp_path)
    with connect(db) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        with pytest.raises(ConcurrentRun):
            run_path(db, path)
    run_path(db, path)
    with connect(db) as conn:
        conn.execute("UPDATE pipeline_ops.runs SET status='running', finished_at=NULL")
    run_path(db, path)
    assert scalar(db, "SELECT count(*) FROM pipeline_ops.runs WHERE error_code='interrupted'") == 1


def test_bounded_transient_retry(db, tmp_path, monkeypatch):
    import sales_pipeline.runner as runner

    real = runner.publish
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise psycopg.errors.SerializationFailure("synthetic failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(runner, "publish", flaky)
    result = run_path(db, source(tmp_path))
    assert result["attempts"] == 3
    assert scalar(db, "SELECT count(*) FROM raw.records") == 16
    assert scalar(db, "SELECT sequence FROM pipeline_ops.checkpoint") == 16


def test_exhausted_retry_has_no_checkpoint(db, tmp_path, monkeypatch):
    import sales_pipeline.runner as runner

    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise psycopg.errors.DeadlockDetected("synthetic failure")

    monkeypatch.setattr(runner, "publish", fail)
    with pytest.raises(psycopg.errors.DeadlockDetected):
        run_path(db, source(tmp_path))
    assert len(calls) == 3
    assert scalar(db, "SELECT sequence FROM pipeline_ops.checkpoint") == 0
    assert scalar(db, "SELECT count(*) FROM raw.records") == 0


def test_source_mutation_and_missing_source_are_failed_runs(db, tmp_path):
    path = source(tmp_path)
    run_path(db, path)
    original = path.read_text()
    path.write_text(original.replace("Synthetic Account 0", "Mutated Account 0"))
    with pytest.raises(ValueError, match="source_journal_mutated"):
        run_path(db, path)
    with pytest.raises(FileNotFoundError):
        run_path(db, tmp_path / "missing.jsonl")
    assert scalar(db, "SELECT count(*) FROM pipeline_ops.runs WHERE status='failed'") == 2
    assert scalar(db, "SELECT sequence FROM pipeline_ops.checkpoint") == 16


def test_empty_history_remains_empty_and_empty_metrics_are_null(db, tmp_path):
    path = source(tmp_path)
    rows = path.read_text().splitlines()
    path.write_text("\n".join(rows[:1]) + "\n")
    run_path(db, path, mode="full")
    assert scalar(db, "SELECT win_rate FROM reporting.closed_performance") is None
    assert scalar(db, "SELECT count(*) FROM reporting.stage_history") == 0
    assert scalar(db, "SELECT sum(value) FROM reporting.open_pipeline") == 0


def test_migrations_are_repeatable_and_detect_tampering(db):
    migrate(db)
    with connect(db) as conn:
        conn.execute(
            "UPDATE pipeline_ops.migrations SET checksum='tampered' WHERE version='001_core.sql'"
        )
    try:
        with pytest.raises(RuntimeError, match="migration_checksum_mismatch"):
            migrate(db)
    finally:
        import hashlib
        from importlib.resources import files

        checksum = hashlib.sha256(
            files("sales_pipeline").joinpath("migrations/001_core.sql").read_bytes()
        ).hexdigest()
        with connect(db) as conn:
            conn.execute(
                "UPDATE pipeline_ops.migrations SET checksum=%s WHERE version='001_core.sql'",
                (checksum,),
            )
