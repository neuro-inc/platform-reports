from datetime import datetime, timedelta
from decimal import Decimal

from neuro_config_client import VolumeConfig

from platform_reports.metrics_service import (
    CreditsUsage,
    CreditsUsageFactory,
    PrometheusQueryFactory,
)
from platform_reports.prometheus_client import Metric
from platform_reports.schema import CategoryName


class TestPrometheusQueryFactory:
    def test_create_compute_credits(self) -> None:
        query = PrometheusQueryFactory().create_compute_credits()

        assert query == (
            "max by(pod) (kube_pod_credits_total) * on(pod) group_right() "
            '(kube_pod_labels{label_platform_neuromation_io_project!=""} or '
            'kube_pod_labels{label_platform_apolo_us_project!=""})'
        )

    def test_create_storage_used(self) -> None:
        query = PrometheusQueryFactory().create_storage_used()

        assert query == "storage_used_bytes"

    def test_create_storage_used__filter_by_org_project(self) -> None:
        query = PrometheusQueryFactory().create_storage_used(
            org_name="test-org", project_name="test-project"
        )

        assert query == (
            'storage_used_bytes{org_name="test-org",project_name="test-project"}'
        )


class TestCreditsUsageFactory:
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

        usage = CreditsUsageFactory().create_for_compute(metric)

        assert usage == CreditsUsage(
            category_name=CategoryName.JOBS,
            project_name="test-project",
            resource_id="test-job",
            credits=Decimal(2),
        )

    def test_create_for_compute__job__with_org_label(self) -> None:
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

        usage = CreditsUsageFactory().create_for_compute(metric)

        assert usage == CreditsUsage(
            category_name=CategoryName.JOBS,
            org_name="test-org",
            project_name="test-project",
            resource_id="test-job",
            credits=Decimal(2),
        )

    def test_create_for_compute__job__no_project_label(self) -> None:
        metric = Metric(
            labels={
                "label_platform_neuromation_io_job": "test-job",
            },
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
                Metric.Value(datetime.now(), Decimal(2)),
                Metric.Value(datetime.now(), Decimal(3)),
            ],
        )

        usage = CreditsUsageFactory().create_for_compute(metric)

        assert usage is None

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

        usage = CreditsUsageFactory().create_for_compute(metric)

        assert usage == CreditsUsage(
            category_name=CategoryName.APPS,
            org_name="test-org",
            project_name="test-project",
            resource_id="test-app",
            credits=Decimal(2),
        )

    def test_create_for_compute__app__no_project_label(self) -> None:
        metric = Metric(
            labels={
                "label_platform_apolo_us_app": "test-app",
            },
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
                Metric.Value(datetime.now(), Decimal(2)),
                Metric.Value(datetime.now(), Decimal(3)),
            ],
        )

        usage = CreditsUsageFactory().create_for_compute(metric)

        assert usage is None

    def test_create_for_compute__not_enough_metrics(self) -> None:
        metric = Metric(
            labels={
                "label_platform_neuromation_io_project": "test-project",
                "label_platform_neuromation_io_job": "test-job",
            },
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
            ],
        )

        usage = CreditsUsageFactory().create_for_compute(metric)

        assert usage is None

    def test_create_for_compute__unknown(self) -> None:
        metric = Metric(
            labels={},
            values=[
                Metric.Value(datetime.now(), Decimal(1)),
                Metric.Value(datetime.now(), Decimal(2)),
            ],
        )

        usage = CreditsUsageFactory().create_for_compute(metric)

        assert usage is None

    def test_create_for_storage(self) -> None:
        volumes: dict[str | None, VolumeConfig] = {
            None: VolumeConfig(name="default", credits_per_hour_per_gb=Decimal(100))
        }
        now = datetime.now()
        metric = Metric(
            labels={"org_name": "test-org", "project_name": "test-project"},
            values=[
                Metric.Value(now, Decimal(1 * 1000**3)),
                Metric.Value(now + timedelta(hours=1), Decimal(2 * 1000**3)),
                Metric.Value(now + timedelta(hours=2), Decimal(3 * 1000**3)),
            ],
        )

        usage = CreditsUsageFactory().create_for_storage(metric, volumes)

        assert usage == CreditsUsage(
            category_name=CategoryName.STORAGE,
            org_name="test-org",
            project_name="test-project",
            resource_id="default",
            credits=Decimal(300),
        )

    def test_create_for_storage__select_project_volume(self) -> None:
        volumes: dict[str | None, VolumeConfig] = {
            None: VolumeConfig(name="default", credits_per_hour_per_gb=Decimal(100)),
            "/test-org/test-project": VolumeConfig(
                name="test-volume", credits_per_hour_per_gb=Decimal(50)
            ),
        }
        now = datetime.now()
        metric = Metric(
            labels={"org_name": "test-org", "project_name": "test-project"},
            values=[
                Metric.Value(now, Decimal(1 * 1000**3)),
                Metric.Value(now + timedelta(hours=1), Decimal(2 * 1000**3)),
                Metric.Value(now + timedelta(hours=2), Decimal(3 * 1000**3)),
            ],
        )

        usage = CreditsUsageFactory().create_for_storage(metric, volumes)

        assert usage == CreditsUsage(
            category_name=CategoryName.STORAGE,
            org_name="test-org",
            project_name="test-project",
            resource_id="test-volume",
            credits=Decimal(150),
        )

    def test_create_for_storage__select_org_volume(self) -> None:
        volumes: dict[str | None, VolumeConfig] = {
            None: VolumeConfig(name="default", credits_per_hour_per_gb=Decimal(100)),
            "/test-org": VolumeConfig(
                name="test-volume", credits_per_hour_per_gb=Decimal(50)
            ),
        }
        now = datetime.now()
        metric = Metric(
            labels={"org_name": "test-org", "project_name": "test-project"},
            values=[
                Metric.Value(now, Decimal(1 * 1000**3)),
                Metric.Value(now + timedelta(hours=1), Decimal(2 * 1000**3)),
                Metric.Value(now + timedelta(hours=2), Decimal(3 * 1000**3)),
            ],
        )

        usage = CreditsUsageFactory().create_for_storage(metric, volumes)

        assert usage == CreditsUsage(
            category_name=CategoryName.STORAGE,
            org_name="test-org",
            project_name="test-project",
            resource_id="test-volume",
            credits=Decimal(150),
        )

    def test_create_for_storage__no_org(self) -> None:
        volumes: dict[str | None, VolumeConfig] = {
            None: VolumeConfig(name="default", credits_per_hour_per_gb=Decimal(100))
        }
        now = datetime.now()
        metric = Metric(
            labels={"org_name": "no_org", "project_name": "test-project"},
            values=[
                Metric.Value(now, Decimal(1 * 1000**3)),
                Metric.Value(now + timedelta(hours=1), Decimal(2 * 1000**3)),
                Metric.Value(now + timedelta(hours=2), Decimal(3 * 1000**3)),
            ],
        )

        usage = CreditsUsageFactory().create_for_storage(metric, volumes)

        assert usage == CreditsUsage(
            category_name=CategoryName.STORAGE,
            project_name="test-project",
            resource_id="default",
            credits=Decimal(300),
        )

    def test_create_for_storage__no_org__select_project_volume(self) -> None:
        volumes: dict[str | None, VolumeConfig] = {
            None: VolumeConfig(name="default", credits_per_hour_per_gb=Decimal(100)),
            "/test-project": VolumeConfig(
                name="test-volume",
                path="/test-project",
                credits_per_hour_per_gb=Decimal(50),
            ),
        }
        now = datetime.now()
        metric = Metric(
            labels={"org_name": "no_org", "project_name": "test-project"},
            values=[
                Metric.Value(now, Decimal(1 * 1000**3)),
                Metric.Value(now + timedelta(hours=1), Decimal(2 * 1000**3)),
                Metric.Value(now + timedelta(hours=2), Decimal(3 * 1000**3)),
            ],
        )

        usage = CreditsUsageFactory().create_for_storage(metric, volumes)

        assert usage == CreditsUsage(
            category_name=CategoryName.STORAGE,
            project_name="test-project",
            resource_id="test-volume",
            credits=Decimal(150),
        )

    def test_create_for_storage__not_enough_metrics(self) -> None:
        volumes: dict[str | None, VolumeConfig] = {
            None: VolumeConfig(name="default", credits_per_hour_per_gb=Decimal(100))
        }
        metric = Metric(
            labels={"org_name": "no_org", "project_name": "test-project"},
            values=[Metric.Value(datetime.now(), Decimal(1 * 1000**3))],
        )

        usage = CreditsUsageFactory().create_for_storage(metric, volumes)

        assert usage is None

    def test_create_for_storage__no_volumes(self) -> None:
        metric = Metric(
            labels={"org_name": "no_org", "project_name": "test-project"},
            values=[Metric.Value(datetime.now(), Decimal(1 * 1000**3))],
        )

        usage = CreditsUsageFactory().create_for_storage(metric, {})

        assert usage is None
