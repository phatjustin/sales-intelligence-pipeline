from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

Stage = Literal["prospecting", "qualification", "proposal", "negotiation", "won", "lost"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("*", mode="after")
    @classmethod
    def canonical_values(cls, value):
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        if isinstance(value, Decimal):
            return value.quantize(Decimal("0.01"))
        return value

    @model_validator(mode="after")
    def ordered(self):
        if self.updated_at < self.created_at:
            raise PydanticCustomError("updated_before_created", "Update precedes creation")
        return self


class Account(Record):
    name: str = Field(min_length=1, max_length=200)


class Opportunity(Record):
    account_id: UUID
    name: str = Field(min_length=1, max_length=200)
    stage: Stage
    value: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    closed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def closed(self):
        if (self.stage in {"won", "lost"}) != (self.closed_at is not None):
            raise PydanticCustomError("closed_at_must_match_stage", "Closed date must match stage")
        if self.closed_at and not self.created_at <= self.closed_at <= self.updated_at:
            raise PydanticCustomError(
                "closed_at_outside_lifecycle", "Closed date outside lifecycle"
            )
        return self


class Activity(Record):
    opportunity_id: UUID
    kind: Literal["email", "meeting", "crm_event"]
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def event_time(self):
        if self.occurred_at > self.updated_at:
            raise PydanticCustomError("activity_after_update", "Activity occurs after update")
        return self


class StageChange(Record):
    opportunity_id: UUID
    from_stage: Stage | None = None
    to_stage: Stage
    changed_at: AwareDatetime

    @model_validator(mode="after")
    def event_time(self):
        if self.changed_at > self.updated_at or self.from_stage == self.to_stage:
            raise PydanticCustomError("invalid_stage_transition", "Stage transition is invalid")
        return self


MODELS = {
    "accounts": Account,
    "opportunities": Opportunity,
    "activities": Activity,
    "stage_changes": StageChange,
}


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone_required")
    return result
