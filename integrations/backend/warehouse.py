"""Read-only pipeline reports. Uses the CRM's existing session and authentication boundary."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sales_intelligence.api.dependencies import Session
from sales_intelligence.services.errors import DomainError

router = APIRouter(prefix="/warehouse", tags=["warehouse"])


class StageMetric(BaseModel):
    stage: str
    opportunity_count: int
    value: Decimal


class ClosedMetric(BaseModel):
    won_count: int
    lost_count: int
    win_rate: Decimal | None
    average_sales_cycle_days: Decimal | None


class Metrics(BaseModel):
    published_at: datetime
    run_id: UUID
    currency: str = "USD"
    open_value: Decimal
    stages: list[StageMetric]
    closed: ClosedMetric


async def rows(session, query, params=None):
    try:
        result = await session.execute(text(query), params or {})
        return result.mappings().all()
    except SQLAlchemyError:
        raise DomainError(
            503, "warehouse_unavailable", "Pipeline reports are unavailable"
        ) from None


@router.get("/metrics", response_model=Metrics)
async def metrics(session: Session):
    # One SQL statement provides one PostgreSQL MVCC snapshot across every report table.
    result = await rows(
        session,
        """
        SELECT p.published_at,p.run_id,s.stage,s.opportunity_count,s.value,
               c.won_count,c.lost_count,c.win_rate,c.average_sales_cycle_days
        FROM reporting.publication p CROSS JOIN reporting.open_pipeline s
        CROSS JOIN reporting.closed_performance c ORDER BY s.stage
    """,
    )
    if not result:
        raise DomainError(503, "warehouse_unavailable", "No complete pipeline publication exists")
    stages = [StageMetric.model_validate(dict(row)) for row in result]
    return Metrics(
        published_at=result[0]["published_at"],
        run_id=result[0]["run_id"],
        stages=stages,
        open_value=sum((s.value for s in stages), Decimal("0.00")),
        closed=ClosedMetric.model_validate(dict(result[0])),
    )


@router.get("/status")
async def status(session: Session) -> dict[str, Any]:
    result = await rows(session, "SELECT * FROM reporting.pipeline_health")
    row = dict(result[0])
    # Synthetic event dates are intentionally old; scheduler freshness uses publication age.
    row["healthy"] = (
        row["status"] == "succeeded"
        and not row["unresolved_rejections"]
        and row["publication_age_seconds"] is not None
        and row["publication_age_seconds"] <= 900
    )
    row["freshness_threshold_seconds"] = 900
    return row


@router.get("/accounts")
async def account_activity(
    session: Session,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    result = await rows(
        session,
        """
        SELECT (SELECT count(*) FROM reporting.account_activity) AS total,
            coalesce((SELECT jsonb_agg(page) FROM (
                SELECT * FROM reporting.account_activity ORDER BY account_id
                LIMIT :limit OFFSET :offset
            ) page),'[]'::jsonb) AS items
    """,
        dict(limit=limit, offset=offset),
    )
    return dict(items=result[0]["items"], total=result[0]["total"], limit=limit, offset=offset)
