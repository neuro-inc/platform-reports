from __future__ import annotations

import enum
import logging
import re
from collections.abc import Iterable, Sequence

from multidict import MultiMapping
from neuro_auth_client import AuthClient, Permission
from neuro_sdk import Client as ApiClient

from .prometheus import Join, LabelMatcherOperator, Metric, Vector, parse_query

logger = logging.getLogger(__name__)


PLATFORM_JOB_RE = re.compile(
    r"^job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class Dashboard(str, enum.Enum):
    NODES = "nodes"
    JOBS = "jobs"
    JOB = "job"
    USER_JOBS = "user_jobs"
    PRICES = "prices"
    SERVICES = "services"


class Matcher(str, enum.Enum):
    JOB = "job"
    POD = "pod"
    USER_LABEL = "label_platform_neuromation_io_user"
    SERVICE_LABEL = "label_service"

    def in_metric(self, metric: Metric) -> bool:
        f = metric.label_matchers.get(self)
        return bool(f and f.operator == LabelMatcherOperator.EQ and f.value)


def get_cluster_manager_permission(cluster_name: str) -> Permission:
    return Permission(uri=f"role://{cluster_name}/manager", action="read")


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
        if dashboard_id == Dashboard.NODES:
            permissions = [get_cluster_manager_permission(self._cluster_name)]
        elif dashboard_id == Dashboard.JOBS:
            permissions = [get_cluster_manager_permission(self._cluster_name)]
        elif dashboard_id == Dashboard.JOB:
            job_id = params.get("var-job_id")
            if job_id and PLATFORM_JOB_RE.match(job_id):
                permissions_service = PermissionsService(
                    self._api_client, self._cluster_name
                )
                permissions = await permissions_service.get_job_permissions([job_id])
            else:
                # If no job id is specified, check that user has access
                # to his own jobs in cluster.
                permissions = [
                    Permission(
                        uri=f"job://{self._cluster_name}/{user_name}", action="read"
                    ),
                ]
        elif dashboard_id == Dashboard.USER_JOBS:
            dashboard_user_name = params.get("var-user_name")
            if dashboard_user_name:
                permissions = [
                    Permission(
                        uri=f"job://{self._cluster_name}/{dashboard_user_name}",
                        action="read",
                    ),
                ]
            else:
                # If no user name is specified, check that user has access
                # to his own jobs in cluster.
                permissions = [
                    Permission(
                        uri=f"job://{self._cluster_name}/{user_name}", action="read"
                    ),
                ]
        elif dashboard_id == Dashboard.PRICES:
            permissions = [get_cluster_manager_permission(self._cluster_name)]
        elif dashboard_id == Dashboard.SERVICES:
            permissions = [get_cluster_manager_permission(self._cluster_name)]
        else:
            return False
        return await self.check_permissions(user_name, permissions)

    async def check_query_permissions(
        self, user_name: str, queries: Sequence[str]
    ) -> bool:
        vectors = self._parse_queries(queries)

        # NOTE: All metrics are required to have a job filter
        # (e.g. kubelet, node-exporter etc). Otherwise we need to have a registry
        # with all the metrics which are exported by Prometheus jobs.
        for vector in vectors:
            if not self._check_all_metrics_have_job_matcher(vector):
                return False

        permissions_service = PermissionsService(self._api_client, self._cluster_name)
        permissions = await permissions_service.get_vector_permissions(vectors)
        return await self.check_permissions(user_name, permissions)

    def _parse_queries(self, queries: Sequence[str]) -> Sequence[Vector]:
        result: list[Vector] = []
        for query in queries:
            vector = parse_query(query)
            if vector:
                result.append(vector)
        return result

    def _check_all_metrics_have_job_matcher(self, vector: Vector) -> bool:
        if isinstance(vector, Metric):
            return Matcher.JOB.in_metric(vector)
        if isinstance(vector, Join):
            return self._check_all_metrics_have_job_matcher(
                vector.left
            ) and self._check_all_metrics_have_job_matcher(vector.right)
        return False


class PermissionsService:
    def __init__(self, api_client: ApiClient, cluster_name: str) -> None:
        self._api_client = api_client
        self._cluster_name = cluster_name
        self._job_permissions: dict[str, Permission] = {}

    async def get_vector_permissions(
        self, vectors: Sequence[Vector]
    ) -> Sequence[Permission]:
        permissions: list[Permission] = []
        for vector in vectors:
            permissions.extend(
                self._get_strongest_permissions(
                    await self._get_vector_permissions(vector)
                )
            )
        result = self._get_strongest_permissions(permissions)
        if get_cluster_manager_permission(self._cluster_name) in result:
            # Other permissions can be removed,
            # cluster manager covers them.
            return [get_cluster_manager_permission(self._cluster_name)]
        return result

    async def _get_vector_permissions(self, vector: Vector) -> Sequence[Permission]:
        if isinstance(vector, Metric):
            return await self._get_metric_permissions(vector)
        if isinstance(vector, Join):
            return await self._get_join_permissions(vector)
        return []

    async def _get_metric_permissions(self, metric: Metric) -> Sequence[Permission]:
        # Check permissions for all collector jobs which are configured in Prometheus
        return (
            *self._get_node_exporter_permissions([metric]),
            *await self._get_kube_state_metrics_permissions([metric]),
            *await self._get_kubelet_permissions([metric]),
            *await self._get_nvidia_dcgm_exporter_permissions([metric]),
            *await self._get_neuro_metrics_exporter_permissions([metric]),
        )

    def _get_node_exporter_permissions(
        self, metrics: Sequence[Metric]
    ) -> list[Permission]:
        for metric in metrics:
            if metric.label_matchers[Matcher.JOB].matches("node-exporter"):
                return [get_cluster_manager_permission(self._cluster_name)]
        return []

    async def _get_kube_state_metrics_permissions(
        self, metrics: Sequence[Metric]
    ) -> list[Permission]:
        permissions: list[Permission] = []
        platform_job_ids: list[str] = []

        for metric in metrics:
            if metric.label_matchers[Matcher.JOB].matches("kube-state-metrics"):
                if Matcher.SERVICE_LABEL.in_metric(metric):
                    return [get_cluster_manager_permission(self._cluster_name)]
                elif self._has_platform_job_matcher(metric):
                    platform_job_ids.append(metric.label_matchers[Matcher.POD].value)
                elif Matcher.USER_LABEL.in_metric(metric):
                    user_name = metric.label_matchers[Matcher.USER_LABEL].value
                    permissions.append(
                        Permission(
                            uri=f"job://{self._cluster_name}/{user_name}", action="read"
                        )
                    )
                else:
                    return [get_cluster_manager_permission(self._cluster_name)]

        return [
            *permissions,
            *await self.get_job_permissions(platform_job_ids),
        ]

    async def _get_kubelet_permissions(
        self, metrics: Sequence[Metric]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for metric in metrics:
            if metric.label_matchers[Matcher.JOB].matches("kubelet"):
                if self._has_platform_job_matcher(metric):
                    platform_job_ids.append(metric.label_matchers[Matcher.POD].value)
                else:
                    return [get_cluster_manager_permission(self._cluster_name)]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_nvidia_dcgm_exporter_permissions(
        self, metrics: Sequence[Metric]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for metric in metrics:
            if metric.label_matchers[Matcher.JOB].matches("nvidia-dcgm-exporter"):
                if self._has_platform_job_matcher(metric):
                    platform_job_ids.append(metric.label_matchers[Matcher.POD].value)
                else:
                    return [get_cluster_manager_permission(self._cluster_name)]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_neuro_metrics_exporter_permissions(
        self, metrics: Sequence[Metric]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for metric in metrics:
            if metric.label_matchers[Matcher.JOB].matches("neuro-metrics-exporter"):
                if self._has_platform_job_matcher(metric):
                    platform_job_ids.append(metric.label_matchers[Matcher.POD].value)
                else:
                    return [get_cluster_manager_permission(self._cluster_name)]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_join_permissions(self, join: Join) -> Sequence[Permission]:
        left_permissions = await self._get_vector_permissions(join.left)
        right_permissions = await self._get_vector_permissions(join.right)
        permissions = (*left_permissions, *right_permissions)

        if "or" == join.operator:
            return self._get_strongest_permissions(permissions)

        if "pod" in join.on:
            return self._get_weakest_permissions(permissions)

        return self._get_strongest_permissions(permissions)

    def _get_strongest_permissions(
        self, permissions: Sequence[Permission]
    ) -> Sequence[Permission]:
        result: list[Permission] = []
        permissions = sorted(permissions, key=lambda p: p.uri)
        i = 0
        while i < len(permissions):
            shortest = permissions[i]
            result.append(shortest)

            while i < len(permissions) and permissions[i].uri.startswith(shortest.uri):
                i += 1
        return result

    def _get_weakest_permissions(
        self, permissions: Sequence[Permission]
    ) -> Sequence[Permission]:
        result: list[Permission] = []
        permissions = sorted(permissions, key=lambda p: p.uri, reverse=True)
        i = 0
        while i < len(permissions):
            longest = permissions[i]
            result.append(longest)

            while i < len(permissions) and longest.uri.startswith(permissions[i].uri):
                i += 1

        # We need to remove cluster manager permission because
        # it covers any permission
        cluster_manager = get_cluster_manager_permission(self._cluster_name)
        if len(result) > 1 and cluster_manager in result:
            result.remove(cluster_manager)

        return result

    def _has_platform_job_matcher(self, metric: Metric) -> bool:
        if not Matcher.POD.in_metric(metric):
            return False
        pod = metric.label_matchers[Matcher.POD].value
        return bool(PLATFORM_JOB_RE.match(pod))

    async def get_job_permissions(
        self, job_ids: Sequence[str], action: str = "read"
    ) -> list[Permission]:
        result: list[Permission] = []

        for job_id in set(job_ids):
            if not job_id:
                continue
            if job_id in self._job_permissions:
                result.append(self._job_permissions[job_id])
            else:
                job = await self._api_client.jobs.status(job_id)
                permission = Permission(uri=str(job.uri), action=action)
                self._job_permissions[job_id] = permission
                result.append(permission)

        return result
