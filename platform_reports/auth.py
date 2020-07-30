import logging
from typing import Iterable, List, Sequence

from multidict import MultiMapping
from neuro_auth_client import AuthClient, Permission
from neuromation.api import Client as ApiClient

from .prometheus import FilterOperator, Metric, parse_query_metrics


logger = logging.getLogger(__name__)


NODES_DASHBOARD_ID = "nodes"
JOBS_DASHBOARD_ID = "jobs"
JOB_DASHBOARD_ID = "job"

JOB_FILTER = "job"
POD_FILTER = "pod"


class AuthService:
    def __init__(
        self, auth_client: AuthClient, api_client: ApiClient, cluster_name: str
    ) -> None:
        self._auth_client = auth_client
        self._api_client = api_client
        self._cluster_name = cluster_name

    async def check_permissions(
        self, user_name: str, permissions: Iterable[Permission]
    ) -> bool:
        permissions = list(set(permissions))
        if not permissions:
            logger.warn("user %r doesn't have any permission to check", user_name)
            return True
        logger.info("checking user %r has permissions %r", user_name, permissions)
        missing_permissions = await self._auth_client.get_missing_permissions(
            user_name, permissions
        )
        if missing_permissions:
            logger.info(
                "user %r doesn't have permissions %r", user_name, missing_permissions
            )
        else:
            logger.info("user %r has permissions %r", user_name, permissions)
        return not missing_permissions

    async def check_dashboard_permissions(
        self, user_name: str, dashboard_id: str, params: MultiMapping[str]
    ) -> bool:
        if dashboard_id == NODES_DASHBOARD_ID:
            permissions = [
                Permission(
                    uri=f"cluster://{self._cluster_name}/admin/cloud_provider/infra",
                    action="read",
                ),
            ]
        elif dashboard_id == JOBS_DASHBOARD_ID:
            permissions = [
                Permission(uri=f"job://{self._cluster_name}", action="read"),
            ]
        elif dashboard_id == JOB_DASHBOARD_ID:
            job_id = params.get("var-job_id")
            if not job_id:
                return True
            permissions = await self._get_platform_job_permissions([job_id])
        else:
            return False
        return await self.check_permissions(user_name, permissions)

    async def check_query_permissions(
        self, user_name: str, queries: Sequence[str]
    ) -> bool:
        metrics = [m for q in queries for m in parse_query_metrics(q)]

        # NOTE: All metrics are required to have a job filter
        # (e.g. kubelet, node-exporter etc). Otherwise we need to have a registry
        # with all the metrics which are exported by Prometheus jobs.
        if not self._check_all_metrics_have_job_filter(metrics):
            return False

        # Check permissions for all collector jobs which are configured in Prometheus
        node_exporter_premissions = self._get_node_exporter_permissions(
            user_name, metrics
        )
        kube_state_metrics_permissions = await self._get_kube_state_metrics_permissions(
            user_name, metrics
        )
        kubelet_permissions = await self._get_kubelet_permissions(user_name, metrics)

        return await self.check_permissions(
            user_name,
            (
                node_exporter_premissions
                + kube_state_metrics_permissions
                + kubelet_permissions
            ),
        )

    def _check_all_metrics_have_job_filter(self, metrics: Sequence[Metric]) -> bool:
        return all(JOB_FILTER in m.filters for m in metrics)

    def _get_node_exporter_permissions(
        self, user_name: str, metrics: Sequence[Metric]
    ) -> List[Permission]:
        for metric in metrics:
            if metric.filters[JOB_FILTER].matches("node-exporter"):
                return [
                    Permission(
                        uri=(
                            f"cluster://{self._cluster_name}/admin/cloud_provider/infra"
                        ),
                        action="read",
                    )
                ]
        return []

    async def _get_kube_state_metrics_permissions(
        self, user_name: str, metrics: Sequence[Metric]
    ) -> List[Permission]:
        platform_job_ids: List[str] = []

        for metric in metrics:
            if metric.filters[JOB_FILTER].matches("kube-state-metrics"):
                if not self._has_pod_filter(metric):
                    return [
                        Permission(uri=f"job://{self._cluster_name}", action="read")
                    ]
                platform_job_ids.append(metric.filters[POD_FILTER].value)

        return await self._get_platform_job_permissions(platform_job_ids)

    async def _get_kubelet_permissions(
        self, user_name: str, metrics: Sequence[Metric]
    ) -> List[Permission]:
        platform_job_ids: List[str] = []

        for metric in metrics:
            if metric.filters[JOB_FILTER].matches("kubelet"):
                if not self._has_pod_filter(metric):
                    return [
                        Permission(uri=f"job://{self._cluster_name}", action="read")
                    ]
                platform_job_ids.append(metric.filters[POD_FILTER].value)

        return await self._get_platform_job_permissions(platform_job_ids)

    def _has_pod_filter(self, metric: Metric) -> bool:
        pod_filter = metric.filters.get(POD_FILTER)
        return bool(
            pod_filter and pod_filter.operator == FilterOperator.EQ and pod_filter.value
        )

    async def _get_platform_job_permissions(
        self, job_ids: Sequence[str], action: str = "read"
    ) -> List[str]:
        result: List[Permission] = []

        for job_id in set(job_ids):
            if job_id:
                job = await self._api_client.jobs.status(job_id)
                result.append(Permission(uri=str(job.uri), action="read"))

        return result
