from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from unittest import mock

import pytest
from multidict import MultiDict
from neuro_auth_client import AuthClient, Permission
from neuro_sdk import Client as ApiClient, JobDescription as Job
from yarl import URL

from platform_reports.auth import AuthService, Dashboard

JOB_ID = "job-00000000-0000-0000-0000-000000000000"


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
            uri=URL(f"job://default/org/project/{id}"),
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

    async def test_cluster_dashboards_permissions(
        self,
        auth_service: AuthService,
        auth_client: mock.AsyncMock,
        cluster_dashboards_expressions: dict[str, Sequence[str]],
    ) -> None:
        assert cluster_dashboards_expressions, "No cluster dashboards found"

        async def get_missing_permissions(
            _: str, permissions: Sequence[Permission]
        ) -> Sequence[Permission]:
            assert all(p.uri in ("role://default/manager",) for p in permissions)
            return []

        auth_client.get_missing_permissions.side_effect = get_missing_permissions

        for _, exprs in cluster_dashboards_expressions.items():
            await auth_service.check_query_permissions("user", exprs)
            auth_client.reset_mock()

    async def test_project_dashboards_permissions(
        self,
        auth_service: AuthService,
        auth_client: mock.AsyncMock,
        project_dashboards_expressions: dict[str, Sequence[str]],
    ) -> None:
        assert project_dashboards_expressions, "No project dashboards found"

        async def get_missing_permissions(
            _: str, permissions: Sequence[Permission]
        ) -> Sequence[Permission]:
            assert all(
                p.uri.startswith("job://default/org/project") for p in permissions
            )
            return []

        auth_client.get_missing_permissions.side_effect = get_missing_permissions

        for _, exprs in project_dashboards_expressions.items():
            await auth_service.check_query_permissions("user", exprs)
            auth_client.reset_mock()

    async def test_org_dashboards_permissions(
        self,
        auth_service: AuthService,
        auth_client: mock.AsyncMock,
        org_dashboards_expressions: dict[str, Sequence[str]],
    ) -> None:
        assert org_dashboards_expressions, "No org dashboards found"

        async def get_missing_permissions(
            _: str, permissions: Sequence[Permission]
        ) -> Sequence[Permission]:
            assert all(p.uri.startswith("job://default/org") for p in permissions)
            return []

        auth_client.get_missing_permissions.side_effect = get_missing_permissions

        for _, exprs in org_dashboards_expressions.items():
            await auth_service.check_query_permissions("user", exprs)
            auth_client.reset_mock()


class TestAuthService:
    @pytest.fixture
    def service(self, auth_client: AuthClient, api_client: ApiClient) -> AuthService:
        return AuthService(auth_client, api_client, "default")

    async def test_check_permissions_is_true(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        result = await service.check_permissions(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )
        assert result is True

    async def test_check_permissions_is_false(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        auth_client.get_missing_permissions.return_value = [
            Permission(uri="role://default/manager", action="read")
        ]

        result = await service.check_permissions(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )
        assert result is False

    async def test_check_nodes_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions("user", Dashboard.NODES, MultiDict())

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri="role://default/manager", action="read")],
        )

    async def test_check_jobs_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions("user", Dashboard.JOBS, MultiDict())

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    async def test_check_job_dashboard_without_job_id_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions("user", Dashboard.JOB, MultiDict())

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="cluster://default/access", action="read")]
        )

    async def test_check_job_dashboard_with_job_id_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_dashboard_permissions(
            "user",
            Dashboard.JOB,
            MultiDict({"var-job_id": JOB_ID}),
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri=f"job://default/org/project/{JOB_ID}", action="read")],
        )
        api_client.jobs.status.assert_awaited_once_with(JOB_ID)

    async def test_check_project_jobs_dashboard_without_project_name_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.PROJECT_JOBS, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="cluster://default/access", action="read")]
        )

    async def test_check_project_jobs_dashboard_with_project_name_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.PROJECT_JOBS, MultiDict({"var-project_name": "project"})
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/project", action="read")]
        )

    async def test_check_org_jobs_dashboard_with_org_name_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.ORG_JOBS, MultiDict({"var-org_name": "org"})
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/org", action="read")]
        )

    async def test_check_org_jobs_dashboard_without_org_name_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.ORG_JOBS, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    async def test_check_prices_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions("user", Dashboard.PRICES, MultiDict())

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_credits_dashboard_with_user_name_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.CREDITS, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    async def test_check_project_credits_dashboard_with_project_name_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user",
            Dashboard.PROJECT_CREDITS,
            MultiDict({"var-project_name": "project"}),
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/project", action="read")]
        )

    async def test_check_project_credits_dashboard_without_project_name_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.PROJECT_CREDITS, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="cluster://default/access", action="read")]
        )

    async def test_check_org_credits_with_org_name_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.ORG_CREDITS, MultiDict({"var-org_name": "org"})
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/org", action="read")]
        )

    async def test_check_org_credits_without_org_name_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.ORG_CREDITS, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    async def test_check_overview_dashboard_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_dashboard_permissions(
            "user", Dashboard.OVERVIEW, MultiDict()
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri="role://default/manager", action="read")],
        )

    async def test_check_node_exporter_query_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user", queries=["node_cpu_seconds_total{job='node-exporter'}"]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri="role://default/manager", action="read")],
        )

    async def test_check_kube_state_metrics_query_without_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user", queries=["kube_pod_labels{job='kube-state-metrics'}"]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_kube_state_metrics_query_with_empty_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["kube_pod_labels{job='kube-state-metrics',pod=''}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_kube_state_metrics_query_with_multiple_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["kube_pod_labels{job='kube-state-metrics',pod=~'.+'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_kube_state_metrics_query_with_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[f"kube_pod_labels{{job='kube-state-metrics',pod='{JOB_ID}'}}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri=f"job://default/org/project/{JOB_ID}", action="read")],
        )
        api_client.jobs.status.assert_awaited_once_with(JOB_ID)

    async def test_check_kube_state_metrics_query_with_service_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                "kube_pod_labels{job='kube-state-metrics',service='platform-storage'}"
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_kubelet_query_without_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["container_cpu_usage_seconds_total{job='kubelet'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_kubelet_query_with_empty_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["container_cpu_usage_seconds_total{job='kubelet',pod=''}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_kubelet_query_with_multiple_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["container_cpu_usage_seconds_total{job='kubelet',pod=~'.+'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_kubelet_query_with_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                f"container_cpu_usage_seconds_total{{job='kubelet',pod='{JOB_ID}'}}"
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri=f"job://default/org/project/{JOB_ID}", action="read")],
        )
        api_client.jobs.status.assert_awaited_once_with(JOB_ID)

    async def test_check_kubelet_query_with_service_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """container_cpu_usage_seconds_total{
                    job='kubelet',
                    pod='platform-service'
                }"""
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri="role://default/manager", action="read")],
        )

    async def test_check_nvidia_dcgm_exporter_query_without_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user", queries=["DCGM_FI_DEV_COUNT{job='nvidia-dcgm-exporter'}"]
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_nvidia_dcgm_exporter_query_with_empty_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["DCGM_FI_DEV_COUNT{job='nvidia-dcgm-exporter',pod=''}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_nvidia_dcgm_exporter_query_with_multiple_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["DCGM_FI_DEV_COUNT{job='nvidia-dcgm-exporter',pod=~'.+'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_nvidia_dcgm_exporter_query_with_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[f"DCGM_FI_DEV_COUNT{{job='nvidia-dcgm-exporter',pod='{JOB_ID}'}}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri=f"job://default/org/project/{JOB_ID}", action="read")],
        )
        api_client.jobs.status.assert_awaited_once_with(JOB_ID)

    async def test_check_neuro_metrics_exporter_query_without_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["node_price_per_hour{job='neuro-metrics-exporter'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_neuro_metrics_exporter_query_with_empty_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["node_price_per_hour{job='neuro-metrics-exporter',pod=''}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_neuro_metrics_exporter_query_with_multiple_pod_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=["node_price_per_hour{job='neuro-metrics-exporter',pod=~'.+'}"],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_neuro_metrics_exporter_query_with_pod_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                f"DCGM_FI_DEV_COUNT{{job='neuro-metrics-exporter',pod='{JOB_ID}'}}"
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri=f"job://default/org/project/{JOB_ID}", action="read")],
        )
        api_client.jobs.status.assert_awaited_once_with(JOB_ID)

    async def test_check_without_job_matcher(self, service: AuthService) -> None:
        result = await service.check_query_permissions(
            user_name="user",
            queries=[f"container_cpu_usage_seconds_total{{pod='{JOB_ID}'}}"],
        )

        assert result is False

    async def test_check_join_for_job_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                f"""
                kube_pod_labels{{job='kube-state-metrics'}}
                * on(pod)
                container_cpu_usage_seconds_total{{job='kubelet',pod='{JOB_ID}'}}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri=f"job://default/org/project/{JOB_ID}", action="read")],
        )
        api_client.jobs.status.assert_awaited_once_with(JOB_ID)

    async def test_check_ignoring_join_for_all_jobs_permissions(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                f"""
                kube_pod_labels{{job='kube-state-metrics'}}
                * ignoring(node)
                container_cpu_usage_seconds_total{{job='kubelet',pod='{JOB_ID}'}}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )
        api_client.jobs.status.assert_awaited_once_with(JOB_ID)

    async def test_check_join_platform_api_called_once(
        self,
        service: AuthService,
        auth_client: mock.AsyncMock,
        api_client: mock.AsyncMock,
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                f"""
                kube_pod_labels{{job='kube-state-metrics',pod='{JOB_ID}'}}
                * on(pod)
                container_cpu_usage_seconds_total{{job='kubelet',pod='{JOB_ID}'}}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user",
            [Permission(uri=f"job://default/org/project/{JOB_ID}", action="read")],
        )
        api_client.jobs.status.assert_awaited_once_with(JOB_ID)

    async def test_check_join_for_project_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{
                    job='kube-state-metrics',
                    label_platform_neuromation_io_project='project'
                }
                * on(pod)
                container_cpu_usage_seconds_total{job='kubelet'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/project", action="read")]
        )

    async def test_check_join_for_org_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{
                    job='kube-state-metrics',
                    label_platform_neuromation_io_org='org'
                }
                * on(pod)
                container_cpu_usage_seconds_total{job='kubelet'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/org", action="read")]
        )

    async def test_check_join_for_no_org_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{
                    job='kube-state-metrics',
                    label_platform_neuromation_io_org='no_org'
                }
                * on(pod)
                container_cpu_usage_seconds_total{job='kubelet'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default", action="read")]
        )

    async def test_check_join_for_org_project_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{
                    job='kube-state-metrics',
                    label_platform_neuromation_io_org='org',
                    label_platform_neuromation_io_project='project'
                }
                * on(pod)
                container_cpu_usage_seconds_total{job='kubelet'}
                """
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="job://default/org/project", action="read")]
        )

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
            "user", [Permission(uri="role://default/manager", action="read")]
        )

    async def test_check_join_without_on_for_all_jobs_permissions(
        self, service: AuthService, auth_client: mock.AsyncMock
    ) -> None:
        await service.check_query_permissions(
            user_name="user",
            queries=[
                """
                kube_pod_labels{job='kube-state-metrics',label_platform_neuromation_io_project='project'}
                *
                container_cpu_usage_seconds_total{job='kubelet'}
                """,
                """
                kube_pod_labels{job='kube-state-metrics',label_platform_neuromation_io_project='project'}
                *
                kube_pod_labels{job='kube-state-metrics',label_platform_neuromation_io_project='project'}
                """,
            ],
        )

        auth_client.get_missing_permissions.assert_awaited_once_with(
            "user", [Permission(uri="role://default/manager", action="read")]
        )

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
            "user", [Permission(uri="role://default/manager", action="read")]
        )
