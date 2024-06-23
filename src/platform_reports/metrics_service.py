import asyncio
import itertools
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .config import PrometheusLabel
from .prometheus_client import Metric, PrometheusClient
from .schema import CategoryName


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreditsConsumptionRequest:
    start_date: datetime
    end_date: datetime
    category_name: CategoryName | None = None
    org_name: str | None = None
    project_name: str | None = None


@dataclass(frozen=True)
class CreditsConsumption:
    category_name: CategoryName
    project_name: str
    resource_id: str
    credits: Decimal
    org_name: str | None = None


class PrometheusQueryFactory:
    @classmethod
    def create_compute_credits(
        cls, *, org_name: str | None = None, project_name: str | None = None
    ) -> str:
        label_matchers = []
        if org_name:
            label_matchers.append(f'label_platform_neuromation_io_org="{org_name}"')
        if project_name:
            label_matchers.append(
                f'label_platform_neuromation_io_project="{project_name}"'
            )
        query = [
            "max by(pod) (kube_pod_credits_total) * on(pod) group_right ",
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
            query.append("kube_pod_labels")
        return "".join(query)

    @classmethod
    def _get_jobs_label_matchers(
        cls, *, org_name: str | None = None, project_name: str | None = None
    ) -> str:
        label_matchers = []
        if org_name:
            label_matchers.append(f'{PrometheusLabel.NEURO_ORG_KEY}="{org_name}"')
        if project_name:
            label_matchers.append(
                f'{PrometheusLabel.NEURO_PROJECT_KEY}="{project_name}"'
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


class CreditsConsumptionFactory:
    @classmethod
    def create_for_compute(cls, metric: Metric) -> CreditsConsumption | None:
        if len(metric.values) < 2:
            return None
        if job_id := metric.labels.get(PrometheusLabel.NEURO_JOB_KEY):
            return cls._create_for_job(metric, job_id=job_id)
        if app_id := metric.labels.get(PrometheusLabel.APOLO_APP_KEY):
            return cls._create_for_app(metric, app_id=app_id)
        LOGGER.warning(
            "Failed to create compute consumption from metric labels: %s", metric.labels
        )
        return None

    @classmethod
    def _create_for_job(cls, metric: Metric, *, job_id: str) -> CreditsConsumption:
        return CreditsConsumption(
            category_name=CategoryName.JOBS,
            org_name=cls._get_org_name(metric),
            project_name=cls._get_project_name(metric),
            resource_id=job_id,
            credits=metric.values[-1].value - metric.values[0].value,
        )

    @classmethod
    def _create_for_app(cls, metric: Metric, *, app_id: str) -> CreditsConsumption:
        return CreditsConsumption(
            category_name=CategoryName.APPS,
            org_name=cls._get_org_name(metric),
            project_name=cls._get_project_name(metric),
            resource_id=app_id,
            credits=metric.values[-1].value - metric.values[0].value,
        )

    @classmethod
    def _get_org_name(cls, metric: Metric) -> str | None:
        org_name = metric.labels.get(
            PrometheusLabel.APOLO_ORG_KEY
        ) or metric.labels.get(PrometheusLabel.NEURO_ORG_KEY)
        return None if org_name == "no_org" else org_name

    @classmethod
    def _get_project_name(cls, metric: Metric) -> str:
        return (
            metric.labels.get(PrometheusLabel.APOLO_PROJECT_KEY)
            or metric.labels[PrometheusLabel.NEURO_PROJECT_KEY]
        )


class MetricsService:
    def __init__(self, *, prometheus_client: PrometheusClient) -> None:
        self._prometheus_client = prometheus_client
        self._prometheus_query_factory = PrometheusQueryFactory()
        self._credits_consumption_factory = CreditsConsumptionFactory()

    async def get_credits_consumption(
        self, request: CreditsConsumptionRequest
    ) -> list[CreditsConsumption]:
        async with asyncio.TaskGroup() as tg:
            tasks = []
            if request.category_name in (None, CategoryName.JOBS, CategoryName.APPS):
                tasks.append(
                    tg.create_task(self._get_compute_credits_consumption(request))
                )
        return list(itertools.chain.from_iterable(t.result() for t in tasks))

    async def _get_compute_credits_consumption(
        self, request: CreditsConsumptionRequest
    ) -> list[CreditsConsumption]:
        LOGGER.debug("Requesting compute credits consumption: %s", request)
        query = self._prometheus_query_factory.create_compute_credits(
            org_name=request.org_name, project_name=request.project_name
        )
        metrics = await self._prometheus_client.evaluate_range_query(
            query=query,
            start_date=request.start_date,
            end_date=request.end_date,
            step=60,
        )
        consumptions = []
        for metric in metrics:
            if c := self._credits_consumption_factory.create_for_compute(metric):
                LOGGER.debug("Compute consumption: %s", c)
                consumptions.append(c)
        return consumptions
