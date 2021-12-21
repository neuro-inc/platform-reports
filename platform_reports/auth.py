from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Sequence
import logging

from multidict import MultiMapping
from neuro_auth_client import AuthClient, Permission
from neuro_sdk import Client as ApiClient

from .prometheus import Join, LabelMatcherOperator, Metric, Vector, parse_query


logger = logging.getLogger(__name__)


NODES_DASHBOARD_ID = "nodes"
JOBS_DASHBOARD_ID = "jobs"
JOB_DASHBOARD_ID = "job"
USER_JOBS_DASHBOARD_ID = "user_jobs"
PRICES_DASHBOARD_ID = "prices"

JOB_MATCHER = "job"
POD_MATCHER = "pod"
USER_LABEL_MATCHER = "label_platform_neuromation_io_user"


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
            if job_id:
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
        elif dashboard_id == USER_JOBS_DASHBOARD_ID:
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
        elif dashboard_id == PRICES_DASHBOARD_ID:
            permissions = [
                Permission(uri=f"job://{self._cluster_name}", action="read"),
            ]
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
        permissions = await permissions_service.get_vector_permissions(
            user_name, vectors
        )
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
            return JOB_MATCHER in vector.label_matchers
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
        self, user_name: str, vectors: Sequence[Vector]
    ) -> Sequence[Permission]:
        permissions: list[Permission] = []
        for vector in vectors:
            permissions.extend(
                self._get_strongest_permissions(
                    await self._get_vector_permissions(user_name, vector)
                )
            )
        return self._get_strongest_permissions(permissions)

    async def _get_vector_permissions(
        self, user_name: str, vector: Vector
    ) -> Sequence[Permission]:
        if isinstance(vector, Metric):
            return await self._get_metric_permissions(user_name, vector)
        if isinstance(vector, Join):
            return await self._get_join_permissions(user_name, vector)
        return []

    async def _get_metric_permissions(
        self, user_name: str, metric: Metric
    ) -> Sequence[Permission]:
        # Check permissions for all collector jobs which are configured in Prometheus
        return (
            *self._get_node_exporter_permissions(user_name, [metric]),
            *await self._get_kube_state_metrics_permissions(user_name, [metric]),
            *await self._get_kubelet_permissions(user_name, [metric]),
            *await self._get_nvidia_dcgm_exporter_permissions(user_name, [metric]),
            *await self._get_neuro_metrics_exporter_permissions(user_name, [metric]),
        )

    def _get_node_exporter_permissions(
        self, user_name: str, metrics: Sequence[Metric]
    ) -> list[Permission]:
        for metric in metrics:
            if metric.label_matchers[JOB_MATCHER].matches("node-exporter"):
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
    ) -> list[Permission]:
        permissions: list[Permission] = []
        platform_job_ids: list[str] = []

        for metric in metrics:
            if metric.label_matchers[JOB_MATCHER].matches("kube-state-metrics"):
                if self._has_pod_matcher(metric):
                    platform_job_ids.append(metric.label_matchers[POD_MATCHER].value)
                elif self._has_user_label_matcher(metric):
                    user_name = metric.label_matchers[USER_LABEL_MATCHER].value
                    permissions.append(
                        Permission(
                            uri=f"job://{self._cluster_name}/{user_name}", action="read"
                        )
                    )
                else:
                    return [
                        Permission(uri=f"job://{self._cluster_name}", action="read")
                    ]

        return [
            *permissions,
            *await self.get_job_permissions(platform_job_ids),
        ]

    async def _get_kubelet_permissions(
        self, user_name: str, metrics: Sequence[Metric]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for metric in metrics:
            if metric.label_matchers[JOB_MATCHER].matches("kubelet"):
                if self._has_pod_matcher(metric):
                    platform_job_ids.append(metric.label_matchers[POD_MATCHER].value)
                else:
                    return [
                        Permission(uri=f"job://{self._cluster_name}", action="read")
                    ]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_nvidia_dcgm_exporter_permissions(
        self, user_name: str, metrics: Sequence[Metric]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for metric in metrics:
            if metric.label_matchers[JOB_MATCHER].matches("nvidia-dcgm-exporter"):
                if self._has_pod_matcher(metric):
                    platform_job_ids.append(metric.label_matchers[POD_MATCHER].value)
                else:
                    return [
                        Permission(uri=f"job://{self._cluster_name}", action="read")
                    ]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_neuro_metrics_exporter_permissions(
        self, user_name: str, metrics: Sequence[Metric]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for metric in metrics:
            if metric.label_matchers[JOB_MATCHER].matches("neuro-metrics-exporter"):
                if self._has_pod_matcher(metric):
                    platform_job_ids.append(metric.label_matchers[POD_MATCHER].value)
                else:
                    return [
                        Permission(uri=f"job://{self._cluster_name}", action="read")
                    ]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_join_permissions(
        self, user_name: str, join: Join
    ) -> Sequence[Permission]:
        left_permissions = await self._get_vector_permissions(user_name, join.left)
        right_permissions = await self._get_vector_permissions(user_name, join.right)
        permissions = (*left_permissions, *right_permissions)

        if "or" == join.operator:
            return self._get_strongest_permissions(permissions)

        if "pod" in join.labels:
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
        return result

    def _has_pod_matcher(self, metric: Metric) -> bool:
        f = metric.label_matchers.get(POD_MATCHER)
        return bool(f and f.operator == LabelMatcherOperator.EQ and f.value)

    def _has_user_label_matcher(self, metric: Metric) -> bool:
        f = metric.label_matchers.get(USER_LABEL_MATCHER)
        return bool(f and f.operator == LabelMatcherOperator.EQ and f.value)

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
