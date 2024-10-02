from datetime import datetime
from decimal import Decimal

from platform_reports.metrics_service import CreditsUsage
from platform_reports.schema import (
    CategoryName,
    PostCreditsUsageRequestSchema,
    PostCreditsUsageResponseSchema,
)


class TestPostCreditsUsageRequestSchema:
    def test_validate__required_fields(self) -> None:
        errors = PostCreditsUsageRequestSchema().validate({})

        assert errors == {
            "end_date": ["Missing data for required field."],
            "start_date": ["Missing data for required field."],
        }

    def test_validate_category_name__incorrect(self) -> None:
        errors = PostCreditsUsageRequestSchema().validate({"category_name": "unknown"})

        assert errors["category_name"][0].startswith("Must be one of:")

    def test_validate_category_name__empty_org(self) -> None:
        errors = PostCreditsUsageRequestSchema().validate({"org_name": ""})

        assert errors["org_name"] == ["Shorter than minimum length 1."]

    def test_validate_category_name__empty_project(self) -> None:
        errors = PostCreditsUsageRequestSchema().validate({"project_name": ""})

        assert errors["project_name"] == ["Shorter than minimum length 1."]

    def test_validate_category_name__incorrect_dates(self) -> None:
        start_date = datetime.now()
        errors = PostCreditsUsageRequestSchema().validate(
            {
                "start_date": start_date.isoformat(),
                "end_date": start_date.isoformat(),
            }
        )

        assert errors["end_date"] == ["end_date must be greater than start_date"]


class TestPostCreditsUsageResponseSchema:
    def test_dump(self) -> None:
        data = PostCreditsUsageResponseSchema().dump(
            CreditsUsage(
                category_name=CategoryName.JOBS,
                org_name="test-org",
                project_name="test-project",
                user_name="test-user",
                resource_id="test-job",
                credits=Decimal(1),
            )
        )

        assert data == {
            "category_name": "jobs",
            "org_name": "test-org",
            "project_name": "test-project",
            "user_name": "test-user",
            "resource_id": "test-job",
            "credits": "1",
        }

    def test_dump__defaults(self) -> None:
        data = PostCreditsUsageResponseSchema().dump(
            CreditsUsage(
                category_name=CategoryName.JOBS,
                project_name="test-project",
                resource_id="test-job",
                credits=Decimal(1),
            )
        )

        assert data == {
            "category_name": "jobs",
            "org_name": None,
            "project_name": "test-project",
            "user_name": None,
            "resource_id": "test-job",
            "credits": "1",
        }
