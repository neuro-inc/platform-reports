from __future__ import annotations

import enum
import logging
import re
from collections.abc import Iterable, Sequence

from multidict import MultiMapping
from neuro_auth_client import AuthClient, Permission

from .platform_api_client import ApiClient
from .platform_apps_client import AppsApiClient
from .prometheus_query_parser import (
    InstantVector,
    LabelMatcher,
    Vector,
    VectorMatch,
    parse_query,
)


logger = logging.getLogger(__name__)


PLATFORM_JOB_RE = re.compile(
    r"^job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class Dashboard(str, enum.Enum):
    NODES = "nodes"
    SERVICES = "services"
    PRICES = "prices"
    OVERVIEW = "overview"
    JOB = "job"
    APP = "app"
    JOBS = "jobs"
    PROJECT_JOBS = "project_jobs"
    ORG_JOBS = "org_jobs"
    CREDITS = "credits"
    PROJECT_CREDITS = "project_credits"
    ORG_CREDITS = "org_credits"


class Matcher(str, enum.Enum):
    JOB = "job"
    POD = "pod"
    ORG_LABEL = "label_platform_neuromation_io_org"
    PROJECT_LABEL = "label_platform_neuromation_io_project"
    APP_INSTANCE_LABEL = "label_platform_apolo_us_app_instance_name"
    SERVICE_LABEL = "label_service"


class AuthService:
    def __init__(
        self,
        auth_client: AuthClient,
        api_client: ApiClient,
        cluster_name: str,
        apps_client: AppsApiClient | None = None,
    ) -> None:
        self._auth_client = auth_client
        self._api_client = api_client
        self._apps_client = apps_client
        self._cluster_name = cluster_name

    async def check_permissions(
        self, user_name: str, permissions: Iterable[Permission]
    ) -> bool:
        permissions = list(set(permissions))
        if not permissions:
            logger.warning("user %r doesn't have any permission to check", user_name)
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

    async def check_dashboard_permissions(  # noqa: C901
        self, user_name: str, dashboard_id: str, params: MultiMapping[str]
    ) -> bool:
        permissions_service = PermissionsService(
            self._api_client, self._cluster_name, self._apps_client
        )
        permissions = []
        if dashboard_id == Dashboard.NODES:
            permissions = [permissions_service.get_cluster_manager_permission()]
        elif dashboard_id == Dashboard.SERVICES:
            permissions = [permissions_service.get_cluster_manager_permission()]
        elif dashboard_id == Dashboard.PRICES:
            permissions = [permissions_service.get_cluster_manager_permission()]
        elif dashboard_id == Dashboard.OVERVIEW:
            permissions = [permissions_service.get_cluster_manager_permission()]
        elif dashboard_id == Dashboard.JOB:
            job_id = params.get("var-job_id")
            if job_id and PLATFORM_JOB_RE.match(job_id):
                permissions = await permissions_service.get_job_permissions([job_id])
        elif dashboard_id == Dashboard.APP:
            app_instance_name = params.get("var-app_instance_name")
            if not app_instance_name:
                # Get access only if app_instance_name is provided
                return False
            permissions = await permissions_service.get_app_permissions(
                [app_instance_name]
            )
        elif dashboard_id == Dashboard.JOBS:
            permissions = [permissions_service.get_job_permission()]
        elif dashboard_id == Dashboard.PROJECT_JOBS:
            dashboard_project_name = params.get("var-project_name")
            if dashboard_project_name:
                permissions = [
                    permissions_service.get_job_permission(
                        project_name=dashboard_project_name
                    )
                ]
        elif dashboard_id == Dashboard.ORG_JOBS:
            dashboard_org_name = params.get("var-org_name")
            permissions = [
                permissions_service.get_job_permission(org_name=dashboard_org_name)
            ]
        elif dashboard_id == Dashboard.CREDITS:
            permissions = [permissions_service.get_job_permission()]
        elif dashboard_id == Dashboard.PROJECT_CREDITS:
            dashboard_project_name = params.get("var-project_name")
            if dashboard_project_name:
                permissions = [
                    permissions_service.get_job_permission(
                        project_name=dashboard_project_name
                    )
                ]
        elif dashboard_id == Dashboard.ORG_CREDITS:
            dashboard_org_name = params.get("var-org_name")
            permissions = [
                permissions_service.get_job_permission(org_name=dashboard_org_name)
            ]
        else:
            return False

        if not permissions:
            # Check user has access to cluster
            permissions.append(
                Permission(uri=f"cluster://{self._cluster_name}/access", action="read")
            )

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

        permissions_service = PermissionsService(
            self._api_client, self._cluster_name, self._apps_client
        )
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
    def __init__(
        self,
        api_client: ApiClient,
        cluster_name: str,
        apps_client: AppsApiClient | None = None,
    ) -> None:
        self._api_client = api_client
        self._apps_client = apps_client
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
                project_matcher = vector.get_eq_label_matcher(Matcher.PROJECT_LABEL)
                app_instance_matcher = vector.get_eq_label_matcher(
                    Matcher.APP_INSTANCE_LABEL
                )

                if app_instance_matcher:
                    permissions.append(
                        self.get_app_permission(
                            org_name=org_matcher.value if org_matcher else None,
                            project_name=project_matcher.value
                            if project_matcher
                            else None,
                        )
                    )
                    return permissions

                if org_matcher or project_matcher:
                    permissions.append(
                        self.get_job_permission(
                            org_name=org_matcher.value if org_matcher else None,
                            project_name=project_matcher.value
                            if project_matcher
                            else None,
                        )
                    )

                # if org_matcher is not None and project_matcher is not None:
                #     permissions.append(
                #         self.get_job_permission(
                #             org_name=org_matcher.value,
                #             project_name=project_matcher.value,
                #         )
                #     )
                # elif org_matcher is not None:
                #     permissions.append(
                #         self.get_job_permission(org_name=org_matcher.value)
                #     )
                # elif project_matcher is not None:
                #     permissions.append(
                #         self.get_job_permission(project_name=project_matcher.value)
                #     )
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
        if matcher is not None and PLATFORM_JOB_RE.match(matcher.value):
            return matcher
        return None

    def get_cluster_manager_permission(self) -> Permission:
        return Permission(uri=f"role://{self._cluster_name}/manager", action="read")

    def get_job_permission(
        self, *, org_name: str | None = None, project_name: str | None = None
    ) -> Permission:
        uri = f"job://{self._cluster_name}"
        if org_name and org_name != "no_org":
            uri = f"{uri}/{org_name}"
        if project_name:
            uri = f"{uri}/{project_name}"
        return Permission(uri=uri, action="read")

    async def get_job_permissions(self, job_ids: Iterable[str]) -> list[Permission]:
        result: list[Permission] = []

        for job_id in set(job_ids):
            if not job_id:
                continue
            if job_id in self._job_permissions:
                result.append(self._job_permissions[job_id])
            else:
                job = await self._api_client.get_job(job_id)
                permission = Permission(uri=str(job.uri), action="read")
                self._job_permissions[job_id] = permission
                result.append(permission)

        return result

    def get_app_permission(
        self, *, org_name: str | None = None, project_name: str | None = None
    ) -> Permission:
        uri = f"app://{self._cluster_name}"
        if org_name and org_name != "no_org":
            uri = f"{uri}/{org_name}"
        if project_name:
            uri = f"{uri}/{project_name}"
        return Permission(uri=uri, action="read")

    async def get_app_permissions(
        self, app_instance_names: Iterable[str]
    ) -> list[Permission]:
        result: list[Permission] = []

        if self._apps_client is None:
            exc_txt = "Apps client is not configured"
            raise Exception(exc_txt)

        for app_instance_name in app_instance_names:
            app = await self._apps_client.get_app_by_name(app_instance_name)
            permission = self.get_app_permission(
                org_name=app.org_name, project_name=app.project_name
            )
            result.append(permission)

        return result
