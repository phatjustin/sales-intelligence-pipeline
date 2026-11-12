"""Measured demo on an isolated database, retained for inspection. No existing tables are reset."""

import json
import tempfile
from pathlib import Path
from uuid import uuid4

from psycopg import sql
from psycopg.conninfo import make_conninfo

from sales_pipeline.db import connect, migrate
from sales_pipeline.runner import InjectedFailure, run_path
from sales_pipeline.synthetic import generate


def demo(url):
    database = f"pipeline_demo_{uuid4().hex[:8]}"
    with connect(url) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    demo_url = make_conninfo(url, dbname=database)
    migrate(demo_url)

    def inspect():
        with connect(demo_url) as conn:
            return dict(
                checkpoint=conn.execute("SELECT sequence FROM pipeline_ops.checkpoint").fetchone()[
                    "sequence"
                ],
                open_value=str(
                    conn.execute(
                        "SELECT sum(value) AS value FROM reporting.open_pipeline"
                    ).fetchone()["value"]
                ),
            )

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "synthetic.jsonl"
        generate(path)
        initial = run_path(demo_url, path, mode="full", page_size=2)
        assert initial["checkpoint_after"] == 16
        assert inspect()["open_value"] == "3000.00"
        generate(path, 1)
        try:
            run_path(demo_url, path, fail_at="after_publish")
        except InjectedFailure:
            pass
        else:
            raise AssertionError("Demo failure was not triggered")
        failed = inspect()
        assert failed == dict(checkpoint=16, open_value="3000.00")
        recovered = run_path(demo_url, path)
        final = inspect()
        assert recovered["rows_applied"] == 3
        assert final == dict(checkpoint=20, open_value="3500.00")
        print(
            json.dumps(
                dict(
                    event="synthetic_demo_verified",
                    database=database,
                    after_failed_run=failed,
                    after_recovery=final,
                )
            ),
            flush=True,
        )
