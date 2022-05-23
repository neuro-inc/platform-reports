from __future__ import annotations

import enum
import logging
import re
from collections.abc import Iterable, Sequence

from multidict import MultiMapping
from neuro_auth_client import AuthClient, Permission
from neuro_sdk import Client as ApiClient

from .prometheus import InstantVector, LabelMatcher, Vector, VectorMatch, parse_query

logger = logging.getLogger(__name__)


PLATFORM_JOB_RE = re.compile(
    r"^job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class Dashboard(str, enum.Enum):
    NODES = "nodes"
    SERVICES = "services"
    PRICES = "prices"
    JOB = "job"
    JOBS = "jobs"
    USER_JOBS = "user_jobs"
    ORG_JOBS = "org_jobs"
    CREDITS = "credits"
    USER_CREDITS = "user_credits"
    ORG_CREDITS = "org_credits"


class Matcher(str, enum.Enum):
    JOB = "job"
    POD = "pod"
    USER_LABEL = "label_platform_neuromation_io_user"
    ORG_LABEL = "label_platform_neuromation_io_org"
    SERVICE_LABEL = "label_service"


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
        permissions_service = PermissionsService(self._api_client, self._cluster_name)

        if dashboard_id == Dashboard.NODES:
            permissions = [permissions_service.get_cluster_manager_permission()]
        elif dashboard_id == Dashboard.SERVICES:
            permissions = [permissions_service.get_cluster_manager_permission()]
        elif dashboard_id == Dashboard.PRICES:
            permissions = [permissions_service.get_cluster_manager_permission()]
        elif dashboard_id == Dashboard.JOB:
            job_id = params.get("var-job_id")
            if job_id and PLATFORM_JOB_RE.match(job_id):
                permissions = await permissions_service.get_job_permissions([job_id])
            else:
                # If no job id is specified, check that user has access
                # to his own jobs in cluster.
                permissions = [
                    permissions_service.get_job_permission(user_name=user_name)
                ]
        elif dashboard_id == Dashboard.JOBS:
            permissions = [permissions_service.get_job_permission()]
        elif dashboard_id == Dashboard.USER_JOBS:
            dashboard_user_name = params.get("var-user_name", user_name)
            permissions = [
                permissions_service.get_job_permission(user_name=dashboard_user_name)
            ]
        elif dashboard_id == Dashboard.ORG_JOBS:
            dashboard_org_name = params.get("var-org_name")
            permissions = [
                permissions_service.get_job_permission(org_name=dashboard_org_name)
            ]
        elif dashboard_id == Dashboard.CREDITS:
            permissions = [permissions_service.get_job_permission()]
        elif dashboard_id == Dashboard.USER_CREDITS:
            dashboard_user_name = params.get("var-user_name", user_name)
            permissions = [
                permissions_service.get_job_permission(user_name=dashboard_user_name)
            ]
        elif dashboard_id == Dashboard.ORG_CREDITS:
            dashboard_org_name = params.get("var-org_name")
            permissions = [
                permissions_service.get_job_permission(org_name=dashboard_org_name)
            ]
        else:
            return False
        return await self.check_permissions(user_name, permissions)

    async def check_query_permissions(
        self, user_name: str, queries: Sequence[str]
    ) -> bool:
        vectors = self._parse_queries(queries)

        # NOTE: All vectors are required to have a job filter
        # (e.g. kubelet, node-exporter etc). Otherwise we need to have a registry
        # with all the vectors which are exported by Prometheus jobs.
        for vector in vectors:
            if not self._check_all_vectors_have_job_matcher(vector):
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

    def _check_all_vectors_have_job_matcher(self, vector: Vector) -> bool:
        if isinstance(vector, InstantVector):
            return Matcher.JOB in vector.label_matchers
        if isinstance(vector, VectorMatch):
            return self._check_all_vectors_have_job_matcher(
                vector.left
            ) and self._check_all_vectors_have_job_matcher(vector.right)
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
        if self.get_cluster_manager_permission() in result:
            # Other permissions can be removed, cluster manager covers them.
            return [self.get_cluster_manager_permission()]
        return result

    async def _get_vector_permissions(self, vector: Vector) -> Sequence[Permission]:
        if isinstance(vector, InstantVector):
            return await self._get_instant_vector_permissions(vector)
        if isinstance(vector, VectorMatch):
            return await self._get_vector_match_permissions(vector)
        return []

    async def _get_instant_vector_permissions(
        self, vector: InstantVector
    ) -> Sequence[Permission]:
        # Check permissions for all collector jobs which are configured in Prometheus
        return (
            *self._get_node_exporter_permissions([vector]),
            *await self._get_kube_state_metrics_permissions([vector]),
            *await self._get_kubelet_permissions([vector]),
            *await self._get_nvidia_dcgm_exporter_permissions([vector]),
            *await self._get_neuro_metrics_exporter_permissions([vector]),
        )

    def _get_node_exporter_permissions(
        self, vectors: Sequence[InstantVector]
    ) -> list[Permission]:
        for vector in vectors:
            if vector.is_from_job("node-exporter"):
                return [self.get_cluster_manager_permission()]
        return []

    async def _get_kube_state_metrics_permissions(
        self, vectors: Sequence[InstantVector]
    ) -> list[Permission]:
        permissions: list[Permission] = []
        platform_job_ids: list[str] = []

        for vector in vectors:
            if vector.is_from_job("kube-state-metrics"):
                matcher = vector.get_eq_label_matcher(Matcher.SERVICE_LABEL)
                if matcher is not None:
                    return [self.get_cluster_manager_permission()]

                matcher = self._get_platform_job_matcher(vector)
                if matcher is not None:
                    platform_job_ids.append(matcher.value)
                    continue

                org_matcher = vector.get_eq_label_matcher(Matcher.ORG_LABEL)
                user_matcher = vector.get_eq_label_matcher(Matcher.USER_LABEL)
                if org_matcher is not None and user_matcher is not None:
                    permissions.append(
                        self.get_job_permission(
                            org_name=org_matcher.value, user_name=user_matcher.value
                        )
                    )
                elif org_matcher is not None:
                    permissions.append(
                        self.get_job_permission(org_name=org_matcher.value)
                    )
                elif user_matcher is not None:
                    permissions.append(
                        self.get_job_permission(user_name=user_matcher.value)
                    )
                else:
                    return [self.get_cluster_manager_permission()]

        return [
            *permissions,
            *await self.get_job_permissions(platform_job_ids),
        ]

    async def _get_kubelet_permissions(
        self, vectors: Sequence[InstantVector]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for vector in vectors:
            if vector.is_from_job("kubelet"):
                matcher = self._get_platform_job_matcher(vector)
                if matcher is not None:
                    platform_job_ids.append(matcher.value)
                else:
                    return [self.get_cluster_manager_permission()]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_nvidia_dcgm_exporter_permissions(
        self, vectors: Sequence[InstantVector]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for vector in vectors:
            if vector.is_from_job("nvidia-dcgm-exporter"):
                matcher = self._get_platform_job_matcher(vector)
                if matcher is not None:
                    platform_job_ids.append(matcher.value)
                else:
                    return [self.get_cluster_manager_permission()]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_neuro_metrics_exporter_permissions(
        self, vectors: Sequence[InstantVector]
    ) -> list[Permission]:
        platform_job_ids: list[str] = []

        for vector in vectors:
            if vector.is_from_job("neuro-metrics-exporter"):
                matcher = self._get_platform_job_matcher(vector)
                if matcher is not None:
                    platform_job_ids.append(matcher.value)
                else:
                    return [self.get_cluster_manager_permission()]

        return await self.get_job_permissions(platform_job_ids)

    async def _get_vector_match_permissions(
        self, vector: VectorMatch
    ) -> Sequence[Permission]:
        left_permissions = await self._get_vector_permissions(vector.left)
        right_permissions = await self._get_vector_permissions(vector.right)
        permissions = (*left_permissions, *right_permissions)

        if "or" == vector.operator:
            return self._get_strongest_permissions(permissions)

        if "pod" in vector.on:
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
        permission = self.get_cluster_manager_permission()
        if len(result) > 1 and permission in result:
            result.remove(permission)

        return result

    def _get_platform_job_matcher(self, vector: InstantVector) -> LabelMatcher | None:
        matcher = vector.get_eq_label_matcher(Matcher.POD)
        if matcher is not None and bool(PLATFORM_JOB_RE.match(matcher.value)):
            return matcher
        return None

    def get_cluster_manager_permission(self) -> Permission:
        return Permission(uri=f"role://{self._cluster_name}/manager", action="read")

    def get_job_permission(
        self, *, org_name: str | None = None, user_name: str | None = None
    ) -> Permission:
        uri = f"job://{self._cluster_name}"
        if org_name and org_name != "no_org":
            uri = f"{uri}/{org_name}"
        if user_name:
            uri = f"{uri}/{user_name}"
        return Permission(uri=uri, action="read")

    async def get_job_permissions(self, job_ids: Iterable[str]) -> list[Permission]:
        result: list[Permission] = []

        for job_id in set(job_ids):
            if not job_id:
                continue
            if job_id in self._job_permissions:
                result.append(self._job_permissions[job_id])
            else:
                job = await self._api_client.jobs.status(job_id)
                permission = Permission(uri=str(job.uri), action="read")
                self._job_permissions[job_id] = permission
                result.append(permission)

        return result
