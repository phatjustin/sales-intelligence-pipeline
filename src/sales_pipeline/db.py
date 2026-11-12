"""Checksummed, transactional SQL migrations independent of the application's Alembic head."""

import hashlib
import time
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

LOCK_ID = 72614021


def connect(url):
    for attempt in range(3):
        try:
            conn = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5)
            conn.execute("SET TIME ZONE 'UTC'")
            return conn
        except psycopg.OperationalError as exc:
            transient = (
                exc.sqlstate is None or exc.sqlstate.startswith("08") or exc.sqlstate == "57P03"
            )
            if not transient or attempt == 2:
                raise
            time.sleep(0.1 * 2**attempt)


def migrate(url):
    with connect(url) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        with conn.transaction():
            conn.execute("CREATE SCHEMA IF NOT EXISTS pipeline_ops")
            conn.execute("""CREATE TABLE IF NOT EXISTS pipeline_ops.migrations (
                version text PRIMARY KEY, checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
            for path in sorted(
                files("sales_pipeline").joinpath("migrations").iterdir(), key=lambda p: p.name
            ):
                if path.name.endswith(".sql"):
                    sql = path.read_text()
                    checksum = hashlib.sha256(sql.encode()).hexdigest()
                    existing = conn.execute(
                        "SELECT checksum FROM pipeline_ops.migrations WHERE version=%s",
                        (path.name,),
                    ).fetchone()
                    if existing and existing["checksum"] != checksum:
                        raise RuntimeError("migration_checksum_mismatch")
                    if not existing:
                        conn.execute(sql)
                        conn.execute(
                            "INSERT INTO pipeline_ops.migrations(version,checksum) VALUES (%s,%s)",
                            (path.name, checksum),
                        )
