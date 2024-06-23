from datetime import datetime

from platform_reports.schema import PostCreditsConsumptionRequestSchema


class TestPostCreditsConsumptionRequestSchema:
    def test__required_fields(self) -> None:
        errors = PostCreditsConsumptionRequestSchema().validate({})

        assert errors == {
            "end_date": ["Missing data for required field."],
            "start_date": ["Missing data for required field."],
        }

    def test_category_name__incorrect(self) -> None:
        errors = PostCreditsConsumptionRequestSchema().validate(
            {"category_name": "unknown"}
        )

        assert errors["category_name"][0].startswith("Must be one of:")

    def test_category_name__empty_org(self) -> None:
        errors = PostCreditsConsumptionRequestSchema().validate({"org_name": ""})

        assert errors["org_name"] == ["Shorter than minimum length 1."]

    def test_category_name__empty_project(self) -> None:
        errors = PostCreditsConsumptionRequestSchema().validate({"project_name": ""})

        assert errors["project_name"] == ["Shorter than minimum length 1."]

    def test_category_name__incorrect_dates(self) -> None:
        start_date = datetime.now()
        errors = PostCreditsConsumptionRequestSchema().validate(
            {
                "start_date": start_date.isoformat(),
                "end_date": start_date.isoformat(),
            }
        )

        assert errors["end_date"] == ["end_date must be greater than start_date"]
