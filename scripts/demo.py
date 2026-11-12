"""Run against the synthetic-only database configured by DATABASE_URL."""

import os

from sales_pipeline.demo import demo

demo(os.environ["DATABASE_URL"])
