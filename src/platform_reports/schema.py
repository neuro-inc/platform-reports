from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, auto, unique
from typing import Any

from marshmallow import Schema, ValidationError, fields, post_load, validates_schema
from marshmallow.validate import Length


class ClientErrorSchema(Schema):
    error = fields.String(required=True)


@unique
class CategoryName(StrEnum):
    JOBS = auto()
    APPS = auto()


@dataclass(frozen=True)
class PostCreditsConsumptionRequest:
    start_date: datetime
    end_date: datetime
    category_name: CategoryName | None = None
    org_name: str | None = None
    project_name: str | None = None


class PostCreditsConsumptionRequestSchema(Schema):
    category_name = fields.Enum(CategoryName, by_value=True)
    org_name = fields.String(validate=[Length(min=1)])
    project_name = fields.String(validate=[Length(min=1)])
    start_date = fields.AwareDateTime(required=True, format="iso", default_timezone=UTC)
    end_date = fields.AwareDateTime(required=True, format="iso", default_timezone=UTC)

    @validates_schema
    def validate_dates(self, data: Mapping[str, Any], **kwargs: Any) -> None:
        if data["start_date"] >= data["end_date"]:
            raise ValidationError(
                {"end_date": ["end_date must be greater than start_date"]}
            )

    @post_load
    def make_object(
        self, data: Mapping[str, Any], **kwargs: Any
    ) -> PostCreditsConsumptionRequest:
        return PostCreditsConsumptionRequest(**data)


class PostCreditsConsumptionResponseSchema(Schema):
    category_name = fields.Enum(CategoryName, by_value=True)
    org_name = fields.String()
    project_name = fields.String()
    resource_id = fields.String(required=True)
    credits = fields.Decimal(required=True, as_string=True)
