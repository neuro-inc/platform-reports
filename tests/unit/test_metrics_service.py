from datetime import datetime
from decimal import Decimal

from platform_reports.metrics_service import (
    CreditsConsumption,
    CreditsConsumptionFactory,
    PrometheusQueryFactory,
)
from platform_reports.prometheus_client import Metric
from platform_reports.schema import CategoryName


class TestPrometheusQueryFactory:
    def test_create_compute_credits(self) -> None:
        query = PrometheusQueryFactory().create_compute_credits()

        assert query == (
            "max by(pod) (kube_pod_credits_total) * "
            "on(pod) group_right kube_pod_labels"
        )

    def test_create_compute_credits__filter_by_org_project(self) -> None:
        query = PrometheusQueryFactory().create_compute_credits(
            org_name="test-org", project_name="test-project"
        )

        assert query == (
            "max by(pod) (kube_pod_credits_total) * on(pod) group_right "
            '(kube_pod_labels{label_platform_neuromation_io_org="test-org",'
            'label_platform_neuromation_io_project="test-project"} or '
            'kube_pod_labels{label_platform_apolo_us_org="test-org",'
            'label_platform_apolo_us_project="test-project"})'
        )


class TestCreditsConsumptionFactory:
    def test_create_for_compute__job(self) -> None:
        metric = Metric(
            labels={
                "label_platform_neuromation_io_project": "test-project",
                "label_platform_neuromation_io_job": "test-job",
            },
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
                Metric.Value(datetime.now(), Decimal(2)),
                Metric.Value(datetime.now(), Decimal(3)),
            ],
        )

        consumption = CreditsConsumptionFactory().create_for_compute(metric)

        assert consumption == CreditsConsumption(
            category_name=CategoryName.JOBS,
            project_name="test-project",
            resource_id="test-job",
            credits=Decimal(2),
        )

    def test_create_for_compute__job__with_org(self) -> None:
        metric = Metric(
            labels={
                "label_platform_neuromation_io_org": "test-org",
                "label_platform_neuromation_io_project": "test-project",
                "label_platform_neuromation_io_job": "test-job",
            },
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
                Metric.Value(datetime.now(), Decimal(2)),
                Metric.Value(datetime.now(), Decimal(3)),
            ],
        )

        consumption = CreditsConsumptionFactory().create_for_compute(metric)

        assert consumption == CreditsConsumption(
            category_name=CategoryName.JOBS,
            org_name="test-org",
            project_name="test-project",
            resource_id="test-job",
            credits=Decimal(2),
        )

    def test_create_for_compute__app(self) -> None:
        metric = Metric(
            labels={
                "label_platform_apolo_us_org": "test-org",
                "label_platform_apolo_us_project": "test-project",
                "label_platform_apolo_us_app": "test-app",
            },
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
                Metric.Value(datetime.now(), Decimal(2)),
                Metric.Value(datetime.now(), Decimal(3)),
            ],
        )

        consumption = CreditsConsumptionFactory().create_for_compute(metric)

        assert consumption == CreditsConsumption(
            category_name=CategoryName.APPS,
            org_name="test-org",
            project_name="test-project",
            resource_id="test-app",
            credits=Decimal(2),
        )

    def test_create_for_compute__none(self) -> None:
        metric = Metric(
            labels={
                "label_platform_neuromation_io_project": "test-project",
                "label_platform_neuromation_io_job": "test-job",
            },
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
            ],
        )

        consumption = CreditsConsumptionFactory().create_for_compute(metric)

        assert consumption is None

    def test_create_for_compute__unknown(self) -> None:
        metric = Metric(
            labels={},
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
                Metric.Value(datetime.now(), Decimal(2)),
            ],
        )

        consumption = CreditsConsumptionFactory().create_for_compute(metric)

        assert consumption is None
