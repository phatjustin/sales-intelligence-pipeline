import argparse
import json
import os

from sales_pipeline.db import migrate
from sales_pipeline.demo import demo
from sales_pipeline.health import health
from sales_pipeline.models import timestamp
from sales_pipeline.runner import acknowledge, run_path
from sales_pipeline.scheduler import schedule
from sales_pipeline.synthetic import generate


def main():
    parser = argparse.ArgumentParser(description="Synthetic CRM PostgreSQL pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate")
    gen.add_argument("--path", default=os.getenv("SOURCE_PATH", "data/crm.jsonl"))
    gen.add_argument("--revision", type=int, choices=[0, 1], default=0)
    commands.add_parser("migrate")
    commands.add_parser("demo")
    load = commands.add_parser("run")
    load.add_argument("--source", default=os.getenv("SOURCE_PATH", "data/crm.jsonl"))
    load.add_argument("--mode", choices=["full", "incremental", "backfill"], default="incremental")
    load.add_argument("--start", type=timestamp)
    load.add_argument("--end", type=timestamp)
    load.add_argument("--page-size", type=int, default=100)
    load.add_argument(
        "--fail-at", choices=["after_staging", "after_publish"], help="Demo failure injection"
    )
    status = commands.add_parser("health")
    status.add_argument("--max-age-seconds", type=int, default=900)
    ack = commands.add_parser("acknowledge")
    ack.add_argument("sequence", type=int)
    ack.add_argument("--note", required=True)
    sched = commands.add_parser("schedule")
    sched.add_argument("--source", default=os.getenv("SOURCE_PATH", "data/crm.jsonl"))
    sched.add_argument("--interval-seconds", type=int, default=300)
    sched.add_argument("--iterations", type=int)
    args = parser.parse_args()
    try:
        if args.command == "generate":
            generate(args.path, args.revision)
            print(json.dumps(dict(event="synthetic_source_generated", revision=args.revision)))
            return
        url = os.environ["DATABASE_URL"]
        if args.command == "migrate":
            migrate(url)
        elif args.command == "demo":
            demo(url)
        elif args.command == "run":
            result = run_path(
                url,
                args.source,
                mode=args.mode,
                start=args.start,
                end=args.end,
                page_size=args.page_size,
                fail_at=args.fail_at,
            )
            raise SystemExit(2 if result["status"] == "partial" else 0)
        elif args.command == "health":
            result = health(url, args.max_age_seconds)
            print(json.dumps(result, default=str))
            raise SystemExit(0 if result["healthy"] else 2)
        elif args.command == "acknowledge":
            acknowledge(url, args.sequence, args.note)
        elif args.command == "schedule":
            schedule(url, args.source, args.interval_seconds, args.iterations)
    except Exception as exc:
        print(json.dumps(dict(event="command_failed", error_code=type(exc).__name__)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
