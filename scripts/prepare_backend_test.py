"""Populate only the dedicated _test database for the optional backend integration tests."""

import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from sales_pipeline.db import migrate
from sales_pipeline.runner import run_path
from sales_pipeline.synthetic import generate

url = os.environ["TEST_DATABASE_URL"]
if not urlparse(url).path.endswith("_test"):
    raise SystemExit("Dedicated _test database required")
migrate(url)
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "crm.jsonl"
    generate(path, 1)
    run_path(url, path, mode="full")
