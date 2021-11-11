from decimal import Decimal
from typing import Callable, Dict, Sequence
from unittest import mock

import pytest
from multidict import MultiDict
from neuro_auth_client import AuthClient, Permission
from neuro_sdk import Client as ApiClient, JobDescription as Job
from yarl import URL

from platform_reports.auth import (
    JOB_DASHBOARD_ID,
    JOBS_DASHBOARD_ID,
    NODES_DASHBOARD_ID,
    PRICES_DASHBOARD_ID,
    USER_JOBS_DASHBOARD_ID,
    AuthService,
)


@pytest.fixture
def job_factory() -> Callable[[str], Job]:
    def _factory(id: str) -> Job:
        return Job(
            id=id,
            owner=None,  # type: ignore
            cluster_name=None,  # type: ignore
            status=None,  # type: ignore
            history=None,  # type: ignore
            container=None,  # type: ignore
            uri=URL(f"job://default/user/{id}"),
            total_price_credits=Decimal("500"),
            price_credits_per_hour=Decimal("5"),
            pass_config=None,  # type: ignore
            scheduler_enabled=False,
        )

    return _factory


@pytest.fixture
def auth_client() -> mock.AsyncMock:
    client = mock.AsyncMock(AuthClient)
    client.get_missing_permissions = mock.AsyncMock(return_value=[])
    return client


@pytest.fixture
def api_client(job_factory: Callable[[str], Job]) -> mock.AsyncMock:
    async def get_job(id: str) -> Job:
        return job_factory(id)

    client = mock.AsyncMock(ApiClient)
    client.jobs.status = mock.AsyncMock(side_effect=get_job)
    return client


class TestDashboards:
    @pytest.fixture
    def auth_service(
        self, auth_client: AuthClient, api_client: ApiClient
    ) -> AuthService:
        return AuthService(auth_client, api_client, "default")

    @pytest.mark.asyncio
    async def test_admin_dashboards_permissions(
        self,
        auth_service: AuthService,
        auth_client: mock.AsyncMock,
        admin_dashboards_expressions: Dict[str, Sequence[str]],
    ) -> None:
        async def get_missing_permissions(
            user_name: str, permissions: Sequence[Permission]
        ) -> Sequence[Permission]:
            assert all(
                p.uri
                in ("cluster://default/admin/cloud_provider/infra", "job://default")
                for p in permissions
            )
            return []

        auth_client.get_missing_permissions.side_effect = get_missing_permissions

        for key, exprs in admin_dashboards_expressions.items():
            await auth_service.check_query_permissions("user", exprs)
            auth_client.reset_mock()

    @pytest.mark.asyncio
    async def test_user_dashboards_permissions(
        self,
        auth_service: AuthService,
        auth_client: mock.AsyncMock,
        user_dashboards_expressions: Dict[str, Sequence[str]],
    ) -> None:
        async def get_missing_permissions(
            user_name: str, permissions: Sequence[Permission]
        ) -> Sequence[Permission]:
            assert all(p.uri.startswith("job://default/user") for p in permissions)
            return []

        auth_client.get_missing_permissions.side_effect = get_missing_permissions

        for key, exprs in user_dashboards_expressions.items():
            await auth_service.check_query_permissions("user", exprs)
            auth_client.reset_mock()


class TestAuthService:
    @pytest.fixture
    def service(self, auth_client: AuthClient, api_client: ApiClient) -> AuthService:
        return AuthService(auth_client, api_client, "default")

    @pytest.mark.asyncio
    async def test_check_permissions_is_true(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        result = await service.check_permissions(
            "user", [Permission(uri="job://default", action="read")]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_check_permissions_is_false(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        auth_client.get_missing_permissions.return_value = [
            Permission(uri="job://default", action="read")
        ]

        result = await service.check_permissions(
            "user", [Permission(uri="job://default", action="read")]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_check_nodes_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", NODES_DASHBOARD_ID, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [
                Permission(
                    uri="cluster://default/admin/cloud_provider/infra", action="read"
                )
            ],
        )

    @pytest.mark.asyncio
    async def test_check_jobs_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", JOBS_DASHBOARD_ID, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_job_dashboard_without_job_id_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions("user", JOB_DASHBOARD_ID, MultiDict())

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_job_dashboard_with_job_id_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_dashboard_permissions(
            "user", JOB_DASHBOARD_ID, MultiDict({"var-job_id": "job"})
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user/job", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with("job")

    @pytest.mark.asyncio
    async def test_check_user_jobs_dashboard_without_user_name_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", USER_JOBS_DASHBOARD_ID, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_user_jobs_dashboard_with_user_name_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_dashboard_permissions(
            "user", USER_JOBS_DASHBOARD_ID, MultiDict({"var-user_name": "other_user"})
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/other_user", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_prices_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", PRICES_DASHBOARD_ID, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_node_exporter_query_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user", queries=["node_cpu_seconds_total{job='node-exporter'}"]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [
                Permission(
                    uri="cluster://default/admin/cloud_provider/infra", action="read"
                )
            ],
        )

    @pytest.mark.asyncio
    async def test_check_kube_state_metrics_query_without_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user", queries=["kube_pod_labels{job='kube-state-metrics'}"]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_kube_state_metrics_query_with_empty_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["kube_pod_labels{job='kube-state-metrics',pod=''}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_kube_state_metrics_query_with_multiple_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["kube_pod_labels{job='kube-state-metrics',pod=~'.+'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_kube_state_metrics_query_with_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["kube_pod_labels{job='kube-state-metrics',pod='job'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user/job", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with("job")

    @pytest.mark.asyncio
    async def test_check_kubelet_query_without_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["container_cpu_usage_seconds_total{job='kubelet'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_kubelet_query_with_empty_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["container_cpu_usage_seconds_total{job='kubelet',pod=''}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_kubelet_query_with_multiple_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["container_cpu_usage_seconds_total{job='kubelet',pod=~'.+'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_kubelet_query_with_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["container_cpu_usage_seconds_total{job='kubelet',pod='job'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user/job", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with("job")

    @pytest.mark.asyncio
    async def test_check_nvidia_dcgm_exporter_query_without_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user", queries=["DCGM_FI_DEV_COUNT{job='nvidia-dcgm-exporter'}"]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_nvidia_dcgm_exporter_query_with_empty_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["DCGM_FI_DEV_COUNT{job='nvidia-dcgm-exporter',pod=''}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_nvidia_dcgm_exporter_query_with_multiple_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["DCGM_FI_DEV_COUNT{job='nvidia-dcgm-exporter',pod=~'.+'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_nvidia_dcgm_exporter_query_with_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["DCGM_FI_DEV_COUNT{job='nvidia-dcgm-exporter',pod='job'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user/job", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with("job")

    @pytest.mark.asyncio
    async def test_check_neuro_metrics_exporter_query_without_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["node_price_per_hour{job='neuro-metrics-exporter'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_neuro_metrics_exporter_query_with_empty_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["node_price_per_hour{job='neuro-metrics-exporter',pod=''}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_neuro_metrics_exporter_query_with_multiple_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["node_price_per_hour{job='neuro-metrics-exporter',pod=~'.+'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_neuro_metrics_exporter_query_with_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["DCGM_FI_DEV_COUNT{job='neuro-metrics-exporter',pod='job'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user/job", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with("job")

    @pytest.mark.asyncio
    async def test_check_without_job_matcher(self, service: AuthService) -> None:
        result = await service.check_query_permissions(
            user_name="user", queries=["container_cpu_usage_seconds_total{pod='job'}"]
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_check_join_for_job_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{job='kube-state-metrics'}
                * on(pod)
                container_cpu_usage_seconds_total{job='kubelet',pod='job'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user/job", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with("job")

    @pytest.mark.asyncio
    async def test_check_ignoring_join_for_all_jobs_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{job='kube-state-metrics'}
                * ignoring(node)
                container_cpu_usage_seconds_total{job='kubelet',pod='job'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with("job")

    @pytest.mark.asyncio
    async def test_check_join_platform_api_called_once(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{job='kube-state-metrics',pod='job'}
                * on(pod)
                container_cpu_usage_seconds_total{job='kubelet',pod='job'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user/job", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with("job")

    @pytest.mark.asyncio
    async def test_check_join_for_user_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{job='kube-state-metrics',label_platform_neuromation_io_user='user'}
                * on(pod)
                container_cpu_usage_seconds_total{job='kubelet'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/user", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_join_for_all_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{job='kube-state-metrics'}
                * on(pod)
                container_cpu_usage_seconds_total{job='kubelet'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_join_without_on_for_all_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{job='kube-state-metrics',label_platform_neuromation_io_user='user'}
                *
                container_cpu_usage_seconds_total{job='kubelet'}
                """,
                """
                kube_pod_labels{job='kube-state-metrics',label_platform_neuromation_io_user='user'}
                *
                kube_pod_labels{job='kube-state-metrics',label_platform_neuromation_io_user='user'}
                """,
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    @pytest.mark.asyncio
    async def test_check_or_join_for_all_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{job='kube-state-metrics',label_platform_neuromation_io_user='user'}
                or on(pod)
                container_cpu_usage_seconds_total{job='kubelet'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )
