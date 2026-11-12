"""Real backend application, original auth dependency, and populated PostgreSQL reports."""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from sales_intelligence.api.app import create_app
from sales_intelligence.db import engine


@pytest.mark.asyncio
async def test_reporting_routes_use_authentication_and_real_reports():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        for path in ("metrics", "status", "accounts"):
            assert (await client.get(f"/api/v1/warehouse/{path}")).status_code == 401
        headers = {"X-API-Key": os.environ["API_KEY"]}
        metrics = await client.get("/api/v1/warehouse/metrics", headers=headers)
        assert metrics.status_code == 200, metrics.text
        assert metrics.json()["open_value"] == "3500.00"
        assert metrics.json()["closed"]["win_rate"].startswith("0.5")
        assert metrics.json()["currency"] == "USD"
        status = await client.get("/api/v1/warehouse/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["status"] == "succeeded"
        assert status.json()["checkpoint_after"] == 20
        accounts = await client.get("/api/v1/warehouse/accounts?limit=1&offset=0", headers=headers)
        assert accounts.status_code == 200
        assert len(accounts.json()["items"]) == 1
        assert accounts.json()["total"] == 2
        empty = await client.get("/api/v1/warehouse/accounts?limit=1&offset=999", headers=headers)
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 2
        assert (
            await client.get("/api/v1/warehouse/accounts?limit=101", headers=headers)
        ).status_code == 422
        assert (await client.post("/api/v1/warehouse/metrics", headers=headers)).status_code == 405
        assert (await client.get("/health")).status_code == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_unpublished_reports_return_a_sanitized_503():
    # Rollback restores the fixture for subsequent manual inspection.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from sales_intelligence.db import get_session

    app = create_app()
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("DELETE FROM reporting.publication"))
            sessions = async_sessionmaker(bind=conn)

            async def override():
                async with sessions() as session:
                    yield session

            app.dependency_overrides[get_session] = override
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/warehouse/metrics", headers={"X-API-Key": os.environ["API_KEY"]}
                )
                assert response.status_code == 503
                assert response.json()["error"]["code"] == "warehouse_unavailable"
            await conn.rollback()
    await engine.dispose()
