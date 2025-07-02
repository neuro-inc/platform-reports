import asyncio
import itertools
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from neuro_config_client import Cluster as ClientCluster, VolumeConfig

from .cluster import ClusterHolder
from .config import PrometheusLabel
from .prometheus_client import Metric, PrometheusClient
from .schema import CategoryName


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GetCreditsUsageRequest:
    start_date: datetime
    end_date: datetime
    category_name: CategoryName | None = None
    org_name: str | None = None
    project_name: str | None = None


@dataclass(frozen=True)
class CreditsUsage:
    category_name: CategoryName
    project_name: str
    resource_id: str
    credits: Decimal
    org_name: str | None = None
    user_name: str | None = None


class PrometheusQueryFactory:
    @classmethod
    def create_compute_credits(
        cls, *, org_name: str | None = None, project_name: str | None = None
    ) -> str:
        query = [
            "max by(pod) (kube_pod_credits_total) * on(pod) group_right() ",
        ]
        if org_name or project_name:
            jobs_label_matchers = cls._get_jobs_label_matchers(
                org_name=org_name, project_name=project_name
            )
            apps_label_matchers = cls._get_apps_label_matchers(
                org_name=org_name, project_name=project_name
            )
            query.append(
                f"(kube_pod_labels{{{jobs_label_matchers}}} or "
                f"kube_pod_labels{{{apps_label_matchers}}})"
            )
        else:
            query.append(
                f'(kube_pod_labels{{{PrometheusLabel.APOLO_PROJECT_KEY}!=""}} or '
                f'kube_pod_labels{{{PrometheusLabel.APOLO_PROJECT_KEY}!=""}})'
            )
        return "".join(query)

    @classmethod
    def _get_jobs_label_matchers(
        cls, *, org_name: str | None = None, project_name: str | None = None
    ) -> str:
        label_matchers = []
        if org_name:
            label_matchers.append(f'{PrometheusLabel.APOLO_ORG_KEY}="{org_name}"')
        if project_name:
            label_matchers.append(
                f'{PrometheusLabel.APOLO_PROJECT_KEY}="{project_name}"'
            )
        return ",".join(label_matchers)

    @classmethod
    def _get_apps_label_matchers(
        cls, *, org_name: str | None = None, project_name: str | None = None
    ) -> str:
        label_matchers = []
        if org_name:
            label_matchers.append(f'{PrometheusLabel.APOLO_ORG_KEY}="{org_name}"')
        if project_name:
            label_matchers.append(
                f'{PrometheusLabel.APOLO_PROJECT_KEY}="{project_name}"'
            )
        return ",".join(label_matchers)

    @classmethod
    def create_storage_used(
        cls, *, org_name: str | None = None, project_name: str | None = None
    ) -> str:
        if org_name or project_name:
            label_matchers = cls._get_storage_used_label_matchers(
                org_name=org_name, project_name=project_name
            )
            return f"storage_used_bytes{{{label_matchers}}}"
        return "storage_used_bytes"

    @classmethod
    def _get_storage_used_label_matchers(
        cls, *, org_name: str | None = None, project_name: str | None = None
    ) -> str:
        label_matchers = []
        if org_name:
            label_matchers.append(f'org_name="{org_name}"')
        if project_name:
            label_matchers.append(f'project_name="{project_name}"')
        return ",".join(label_matchers)


class Cluster:
    def __init__(self, cluster: ClientCluster) -> None:
        self._volumes = {}

        if not cluster.storage:
            LOGGER.warning("Cluster storage is not available, check token permissions")
            return

        for volume in cluster.storage.volumes:
            self._volumes[volume.path or None] = volume

    @property
    def has_volumes(self) -> bool:
        return len(self._volumes) > 0

    def get_project_volume(
        self, *, org_name: str | None = None, project_name: str
    ) -> VolumeConfig | None:
        if org_name:
            if volume := self._volumes.get(f"/{org_name}/{project_name}"):
                return volume
            if volume := self._volumes.get(f"/{org_name}"):
                return volume
        elif volume := self._volumes.get(f"/{project_name}"):
            return volume
        return self._volumes.get(None)


class PodCreditsMetric(Metric):
    @property
    def org_name(self) -> str | None:
        org_name = self.labels.get(PrometheusLabel.APOLO_ORG_KEY) or self.labels.get(
            PrometheusLabel.APOLO_ORG_KEY
        )
        return None if org_name == "no_org" else org_name

    @property
    def project_name(self) -> str | None:
        return self.labels.get(PrometheusLabel.APOLO_PROJECT_KEY) or self.labels.get(
            PrometheusLabel.APOLO_PROJECT_KEY
        )

    @property
    def user_name(self) -> str | None:
        return self.labels.get(PrometheusLabel.APOLO_USER_KEY) or self.labels.get(
            PrometheusLabel.NEURO_USER_KEY
        )


class StorageUsedMetric(Metric):
    @property
    def org_name(self) -> str | None:
        org_name = self.labels["org_name"]
        return None if org_name == "no_org" else org_name

    @property
    def project_name(self) -> str:
        return self.labels["project_name"]


class CreditsUsageFactory:
    @classmethod
    def create_for_compute(cls, metric: PodCreditsMetric) -> CreditsUsage | None:
        if len(metric.values) < 2:
            return None
        if job_id := metric.labels.get(PrometheusLabel.NEURO_JOB_KEY):
            return cls._create_for_job(metric, job_id=job_id)
        if app_id := metric.labels.get(PrometheusLabel.APOLO_APP_INSTANCE_NAME_KEY):
            return cls._create_for_app(metric, app_id=app_id)
        LOGGER.warning(
            "Failed to create compute credits usage from metric labels: %s",
            metric.labels,
        )
        return None

    @classmethod
    def _create_for_job(
        cls, metric: PodCreditsMetric, *, job_id: str
    ) -> CreditsUsage | None:
        if not (project_name := metric.project_name):
            return None
        return CreditsUsage(
            category_name=CategoryName.JOBS,
            org_name=metric.org_name,
            project_name=project_name,
            user_name=metric.user_name,
            resource_id=job_id,
            credits=metric.values[-1].value - metric.values[0].value,
        )

    @classmethod
    def _create_for_app(
        cls, metric: PodCreditsMetric, *, app_id: str
    ) -> CreditsUsage | None:
        if not (project_name := metric.project_name):
            return None
        return CreditsUsage(
            category_name=CategoryName.APPS,
            org_name=metric.org_name,
            project_name=project_name,
            user_name=metric.user_name,
            resource_id=app_id,
            credits=metric.values[-1].value - metric.values[0].value,
        )

    @classmethod
    def create_for_storage(
        cls, metric: StorageUsedMetric, cluster: Cluster
    ) -> CreditsUsage | None:
        if len(metric.values) < 2:
            return None
        volume = cluster.get_project_volume(
            org_name=metric.org_name, project_name=metric.project_name
        )
        if not volume:
            return None
        credits_sum = Decimal(0)
        prev_value = metric.values[0]
        for curr_value in itertools.islice(metric.values, 1, None):
            credits_sum += (
                (prev_value.value / 1000**3)
                * (Decimal((curr_value.time - prev_value.time).total_seconds()) / 3600)
                * volume.credits_per_hour_per_gb
            )
            prev_value = curr_value
        return CreditsUsage(
            category_name=CategoryName.STORAGE,
            org_name=metric.org_name,
            project_name=metric.project_name,
            resource_id=volume.name,
            credits=credits_sum,
        )


class MetricsService:
    def __init__(
        self, *, prometheus_client: PrometheusClient, cluster_holder: ClusterHolder
    ) -> None:
        self._prometheus_client = prometheus_client
        self._prometheus_query_factory = PrometheusQueryFactory()
        self._credits_usage_factory = CreditsUsageFactory()
        self._cluster_holder = cluster_holder

    async def get_credits_usage(
        self, request: GetCreditsUsageRequest
    ) -> list[CreditsUsage]:
        async with asyncio.TaskGroup() as tg:
            tasks = []
            if request.category_name in (None, CategoryName.JOBS, CategoryName.APPS):
                tasks.append(tg.create_task(self._get_compute_credits_usage(request)))
            if request.category_name in (None, CategoryName.STORAGE):
                tasks.append(tg.create_task(self._get_storage_credits_usage(request)))
        return list(itertools.chain.from_iterable(t.result() for t in tasks))

    async def _get_compute_credits_usage(
        self, request: GetCreditsUsageRequest
    ) -> list[CreditsUsage]:
        LOGGER.debug("Requesting compute credits usage: %s", request)
        query = self._prometheus_query_factory.create_compute_credits(
            org_name=request.org_name, project_name=request.project_name
        )
        metrics = await self._prometheus_client.evaluate_range_query(
            query=query,
            start_date=request.start_date,
            end_date=request.end_date,
            metric_cls=PodCreditsMetric,
        )
        usage = []
        for metric in metrics:
            if c := self._credits_usage_factory.create_for_compute(metric):
                LOGGER.debug("Compute credits usage: %s", c)
                usage.append(c)
        return usage

    async def _get_storage_credits_usage(
        self, request: GetCreditsUsageRequest
    ) -> list[CreditsUsage]:
        storage = Cluster(self._cluster_holder.cluster)
        if not storage.has_volumes:
            return []

        LOGGER.debug("Requesting storage credits usage: %s", request)
        query = self._prometheus_query_factory.create_storage_used(
            org_name=request.org_name, project_name=request.project_name
        )
        metrics = await self._prometheus_client.evaluate_range_query(
            query=query,
            start_date=request.start_date,
            end_date=request.end_date,
            metric_cls=StorageUsedMetric,
        )
        usage = []
        for metric in metrics:
            if c := self._credits_usage_factory.create_for_storage(metric, storage):
                LOGGER.debug("Storage credits usage: %s", c)
                usage.append(c)
        return usage
