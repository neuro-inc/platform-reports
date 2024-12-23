from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from decimal import Decimal
from importlib.metadata import version
from textwrap import dedent

import aiobotocore.session
import aiohttp
import aiohttp.web
import uvloop
from aiohttp.web import (
    HTTPBadRequest,
    HTTPForbidden,
    HTTPInternalServerError,
    HTTPOk,
    Request,
    Response,
    StreamResponse,
    json_response,
    middleware,
)
from aiohttp.web_urldispatcher import AbstractRoute
from aiohttp_apispec import (
    docs,
    request_schema,
    response_schema,
    setup_aiohttp_apispec,
    validation_middleware,
)
from jose import jwt
from multidict import CIMultiDict, MultiMapping
from neuro_auth_client import AuthClient, Permission, check_permissions
from neuro_auth_client.security import setup_security
from neuro_config_client.client import ConfigClient
from neuro_logging import init_logging, setup_sentry
from yarl import URL

from .auth import AuthService
from .cluster import RefreshableClusterHolder
from .config import (
    EnvironConfigFactory,
    GrafanaProxyConfig,
    Label,
    MetricsApiConfig,
    MetricsExporterConfig,
    PlatformServiceConfig,
    PrometheusProxyConfig,
)
from .kube_client import KubeClient
from .metrics_collector import (
    AWSNodePriceCollector,
    AzureNodePriceCollector,
    Collector,
    ConfigPriceCollector,
    GCPNodePriceCollector,
    NodeEnergyConsumptionCollector,
    PodCreditsCollector,
    Price,
)
from .metrics_service import GetCreditsUsageRequest, MetricsService
from .platform_api_client import ApiClient
from .prometheus_client import PrometheusClient
from .schema import (
    ClientErrorSchema,
    PostCreditsUsageRequest,
    PostCreditsUsageRequestSchema,
    PostCreditsUsageResponseSchema,
)


LOGGER = logging.getLogger(__name__)

METRICS_EXPORTER_CONFIG_APP_KEY = aiohttp.web.AppKey("config", MetricsExporterConfig)
PROMETHEUS_PROXY_APP_KEY = aiohttp.web.AppKey("config", PrometheusProxyConfig)
GRAFANA_PROXY_APP_KEY = aiohttp.web.AppKey("config", GrafanaProxyConfig)
NODE_PRICE_COLLECTOR_APP_KEY = aiohttp.web.AppKey(
    "node_price_collector", Collector[Price]
)
POD_CREDITS_COLLECTOR_APP_KEY = aiohttp.web.AppKey(
    "pod_credits_collector", Collector[Mapping[str, Decimal]]
)
NODE_POWER_CONSUMPTION_COLLECTOR_APP_KEY = aiohttp.web.AppKey(
    "node_power_consumption_collector", NodeEnergyConsumptionCollector
)
PROMETHEUS_CLIENT_APP_KEY = aiohttp.web.AppKey(
    "prometheus_client", aiohttp.ClientSession
)
GRAFANA_CLIENT_APP_KEY = aiohttp.web.AppKey("grafana_client", aiohttp.ClientSession)
AUTH_SERVICE_APP_KEY = aiohttp.web.AppKey("auth_service", AuthService)
METRICS_API_CONFIG_APP_KEY = aiohttp.web.AppKey("config", MetricsApiConfig)
METRICS_SERVICE_APP_KEY = aiohttp.web.AppKey("metrics_service", MetricsService)


class ProbesHandler:
    def __init__(self, app: aiohttp.web.Application) -> None:
        self._app = app

    def register(self) -> list[AbstractRoute]:
        return self._app.router.add_routes([aiohttp.web.get("/ping", self.handle_ping)])

    async def handle_ping(self, request: Request) -> Response:
        return Response(text="Pong")


class MetricsExporterHandler:
    def __init__(self, app: aiohttp.web.Application) -> None:
        self._app = app

    def register(self) -> None:
        self._app.router.add_get("/metrics", self.handle)

    @property
    def _node_price_collector(self) -> Collector[Price]:
        return self._app[NODE_PRICE_COLLECTOR_APP_KEY]

    @property
    def _pod_credits_collector(self) -> Collector[Mapping[str, Decimal]]:
        return self._app[POD_CREDITS_COLLECTOR_APP_KEY]

    @property
    def _node_power_consumption_collector(self) -> NodeEnergyConsumptionCollector:
        return self._app[NODE_POWER_CONSUMPTION_COLLECTOR_APP_KEY]

    @property
    def _config(self) -> MetricsExporterConfig:
        return self._app[METRICS_EXPORTER_CONFIG_APP_KEY]

    async def handle(self, request: Request) -> Response:
        text = [self._get_node_price_total_text()]
        pod_credits_text = self._get_pod_credits_total_text()
        if pod_credits_text:
            text.append(pod_credits_text)
        text.append(self._get_node_power_usage_text())
        return Response(text="\n\n".join(text))

    def _get_node_price_total_text(self) -> str:
        node = self._config.node_name
        price = self._node_price_collector.current_value
        return dedent(
            f"""\
            # HELP kube_node_price_total The total price of the node.
            # TYPE kube_node_price_total counter
            kube_node_price_total{{node="{node}",currency="{price.currency}"}} {price.value}"""  # noqa: E501
        )

    def _get_pod_credits_total_text(self) -> str:
        pod_credits_total = self._pod_credits_collector.current_value
        if not pod_credits_total:
            return ""
        metrics: list[str] = [
            dedent(
                """\
                # HELP kube_pod_credits_total The total credits consumed by the pod.
                # TYPE kube_pod_credits_total counter"""
            )
        ]
        for name, pod_credits in pod_credits_total.items():
            metrics.append(f'kube_pod_credits_total{{pod="{name}"}} {pod_credits}')
        return "\n".join(metrics)

    def _get_node_power_usage_text(self) -> str:
        node = self._config.node_name
        consumption = self._node_power_consumption_collector.current_value
        return dedent(
            f"""\
            # HELP cpu_min_watts The CPU power consumption while IDLEing in watts
            # TYPE cpu_min_watts gauge
            cpu_min_watts{{node="{node}"}} {consumption.cpu_min_watts}
            # HELP cpu_max_watts The CPU power consumption when fully utilized in watts
            # TYPE cpu_max_watts gauge
            cpu_max_watts{{node="{node}"}} {consumption.cpu_max_watts}
            # HELP co2_grams_eq_per_kwh Estimated CO2 emission for energy generation in region where the node is running
            # TYPE co2_grams_eq_per_kwh gauge
            co2_grams_eq_per_kwh{{node="{node}"}} {consumption.co2_grams_eq_per_kwh}
            # HELP price_per_kwh Energy price per kwh in region where the node is running
            # TYPE price_per_kwh gauge
            price_per_kwh{{node="{node}"}} {consumption.price_per_kwh}"""  # noqa: E501
        )


class PrometheusProxyHandler:
    def __init__(self, app: aiohttp.web.Application) -> None:
        self._app = app

    def register(self) -> None:
        self._app.router.add_get("/{sub_path:.*}", self.handle)

    @property
    def _config(self) -> PrometheusProxyConfig:
        return self._app[PROMETHEUS_PROXY_APP_KEY]

    @property
    def _prometheus_client(self) -> aiohttp.ClientSession:
        return self._app[PROMETHEUS_CLIENT_APP_KEY]

    @property
    def _auth_service(self) -> AuthService:
        return self._app[AUTH_SERVICE_APP_KEY]

    async def handle(self, request: Request) -> StreamResponse:
        user_name = _get_user_name(request, self._config.access_token_cookie_names)

        # /query or /query_range
        if request.match_info["sub_path"].startswith("query"):
            query = request.query["query"]
            if not await self._auth_service.check_query_permissions(user_name, [query]):
                return Response(status=HTTPForbidden.status_code)
        elif request.match_info["sub_path"] == "series":
            queries = request.query.getall("match[]")
            if not await self._auth_service.check_query_permissions(user_name, queries):
                return Response(status=HTTPForbidden.status_code)
        else:
            # Potentially user can request any data in prometheus.
            # We need to check the maximum set of permissions which are required
            # to access any data in prometheus.
            if not await self._auth_service.check_permissions(
                user_name,
                [
                    Permission(
                        uri=f"role://{self._config.cluster_name}/manager",
                        action="read",
                    ),
                ],
            ):
                return Response(status=HTTPForbidden.status_code)

        return await _proxy_request(
            client=self._prometheus_client,
            upstream_url=self._config.prometheus_url,
            request=request,
        )


class GrafanaProxyHandler:
    def __init__(self, app: aiohttp.web.Application) -> None:
        self._app = app

    def register(self) -> None:
        self._app.router.add_get(
            "/api/dashboards/uid/{dashboard_id}", self.handle_get_dashboard
        )
        self._app.router.add_route("*", "/{sub_path:.*}", self.handle)

    @property
    def _config(self) -> GrafanaProxyConfig:
        return self._app[GRAFANA_PROXY_APP_KEY]

    @property
    def _grafana_client(self) -> aiohttp.ClientSession:
        return self._app[GRAFANA_CLIENT_APP_KEY]

    @property
    def _auth_service(self) -> AuthService:
        return self._app[AUTH_SERVICE_APP_KEY]

    async def handle(self, request: Request) -> StreamResponse:
        user_name = _get_user_name(request, self._config.access_token_cookie_names)

        # Check that user has access to his own jobs in cluster.
        if not await self._auth_service.check_permissions(
            user_name,
            [
                Permission(
                    uri=f"cluster://{self._config.cluster_name}/access",
                    action="read",
                )
            ],
        ):
            return Response(status=HTTPForbidden.status_code)

        return await _proxy_request(
            client=self._grafana_client,
            upstream_url=self._config.grafana_url,
            request=request,
        )

    async def handle_get_dashboard(self, request: Request) -> StreamResponse:
        user_name = _get_user_name(request, self._config.access_token_cookie_names)
        dashboard_id = request.match_info["dashboard_id"]

        if not await self._auth_service.check_dashboard_permissions(
            user_name=user_name, dashboard_id=dashboard_id, params=request.query
        ):
            return Response(status=HTTPForbidden.status_code)

        return await _proxy_request(
            client=self._grafana_client,
            upstream_url=self._config.grafana_url,
            request=request,
        )


class MetricsApiHandler:
    def __init__(self, app: aiohttp.web.Application) -> None:
        self._app = app

    def register(self) -> None:
        self._app.router.add_post(
            "/v1/metrics/credits/usage", self.handle_post_credits_usage
        )

    @property
    def _config(self) -> MetricsApiConfig:
        return self._app[METRICS_API_CONFIG_APP_KEY]

    @property
    def _metrics_service(self) -> MetricsService:
        return self._app[METRICS_SERVICE_APP_KEY]

    @docs(
        tags=["Metrics"],
        summary="Evaluate credits usage.",
        responses={
            HTTPOk.status_code: {},
            HTTPInternalServerError.status_code: {
                "description": "Unhandled error",
                "schema": ClientErrorSchema(),
            },
        },
    )
    @request_schema(PostCreditsUsageRequestSchema())
    @response_schema(PostCreditsUsageResponseSchema(many=True))
    async def handle_post_credits_usage(self, request: Request) -> Response:
        await check_permissions(
            request, [Permission(f"cluster://{self._config.cluster_name}", "read")]
        )
        request_data: PostCreditsUsageRequest = request["data"]
        usage = await self._metrics_service.get_credits_usage(
            GetCreditsUsageRequest(
                category_name=request_data.category_name,
                org_name=request_data.org_name,
                project_name=request_data.project_name,
                start_date=request_data.start_date,
                end_date=request_data.end_date,
            )
        )
        response_schema = PostCreditsUsageResponseSchema(many=True)
        return json_response(response_schema.dump(usage), status=HTTPOk.status_code)


def _get_user_name(request: Request, access_token_cookie_names: Sequence[str]) -> str:
    access_token: str = ""
    for cookie_name in access_token_cookie_names:
        access_token = request.cookies.get(cookie_name, "")
        if access_token:
            break
    if not access_token:
        msg = "Request doesn't have access token cookie"
        raise ValueError(msg)
    claims = jwt.get_unverified_claims(access_token)
    return claims["https://platform.neuromation.io/user"]


async def _proxy_request(
    client: aiohttp.ClientSession, upstream_url: URL, request: Request
) -> StreamResponse:
    assert upstream_url.host
    upstream_url = (
        request.url.with_scheme(upstream_url.scheme)
        .with_host(upstream_url.host)
        .with_port(upstream_url.port)
    )
    upstream_request_headers = _prepare_upstream_request_headers(request.headers)

    if request.method == "HEAD":
        data = None
    else:
        data = request.content.iter_any()

    async with client.request(
        method=request.method,
        url=upstream_url,
        headers=upstream_request_headers,
        skip_auto_headers=("Content-Type",),
        allow_redirects=False,
        data=data,
    ) as upstream_response:
        LOGGER.debug("upstream response: %s", upstream_response)

        response = aiohttp.web.StreamResponse(
            status=upstream_response.status, headers=upstream_response.headers.copy()
        )

        await response.prepare(request)

        LOGGER.debug("response: %s; headers: %s", response, response.headers)

        async for chunk in upstream_response.content.iter_any():
            await response.write(chunk)

        await response.write_eof()
        return response


def _prepare_upstream_request_headers(headers: MultiMapping[str]) -> CIMultiDict[str]:
    request_headers = CIMultiDict(headers)

    for name in ("Transfer-Encoding", "Connection"):
        request_headers.pop(name, None)

    return request_headers


@middleware
async def handle_exceptions(
    request: Request, handler: Callable[[Request], Awaitable[StreamResponse]]
) -> StreamResponse:
    try:
        return await handler(request)
    except ValueError as e:
        payload = {"error": str(e)}
        return json_response(payload, status=HTTPBadRequest.status_code)
    except aiohttp.web.HTTPException:
        raise
    except (Exception, ExceptionGroup) as e:
        if isinstance(e, ExceptionGroup):
            e = e.exceptions[0]
        msg_str = (
            f"Unexpected exception: {str(e)}. " f"Path with query: {request.path_qs}."
        )
        logging.exception(msg_str)
        payload = {"error": msg_str}
        return json_response(payload, status=HTTPInternalServerError.status_code)


@asynccontextmanager
async def run_task(coro: Awaitable[None]) -> AsyncIterator[None]:
    task: asyncio.Task[None] = asyncio.create_task(coro)  # type: ignore
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@asynccontextmanager
async def create_api_client(config: PlatformServiceConfig) -> AsyncIterator[ApiClient]:
    async with ApiClient(url=config.url, token=config.token) as client:
        yield client


def get_aws_pricing_api_region(region: str) -> str:
    # Only two endpoints are supported by AWS
    # https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/Welcome.html
    if region.startswith("ap"):
        return "ap-south-1"
    return "us-east-1"


package_version = version(__package__)


async def add_version_to_header(request: Request, response: StreamResponse) -> None:
    response.headers["X-Service-Version"] = f"platform-reports/{package_version}"


def create_metrics_exporter_app(
    config: MetricsExporterConfig,
) -> aiohttp.web.Application:
    app = aiohttp.web.Application()
    ProbesHandler(app).register()
    MetricsExporterHandler(app).register()

    app[METRICS_EXPORTER_CONFIG_APP_KEY] = config

    async def _init_app(app: aiohttp.web.Application) -> AsyncIterator[None]:
        async with AsyncExitStack() as exit_stack:
            LOGGER.info("Initializing Config client")
            config_client = await exit_stack.enter_async_context(
                ConfigClient(config.platform_config.url, config.platform_config.token)
            )
            cluster_holder = await exit_stack.enter_async_context(
                RefreshableClusterHolder(
                    config_client=config_client, cluster_name=config.cluster_name
                )
            )

            LOGGER.info("Initializing Kube client")
            kube_client = await exit_stack.enter_async_context(KubeClient(config.kube))

            node = await kube_client.get_node(config.node_name)
            zone = (
                node.metadata.labels.get("failure-domain.beta.kubernetes.io/zone")
                or node.metadata.labels.get("topology.kubernetes.io/zone")
                or ""
            )
            LOGGER.info("Node zone: %s", zone)

            instance_type = (
                node.metadata.labels.get("node.kubernetes.io/instance-type")
                or node.metadata.labels.get("beta.kubernetes.io/instance-type")
                or ""
            )
            LOGGER.info("Node instance type: %s", instance_type)

            is_preemptible = Label.NEURO_PREEMPTIBLE_KEY in node.metadata.labels
            LOGGER.info("Node is preemptible: %s", is_preemptible)

            node_pool_name = node.metadata.labels[Label.NEURO_NODE_POOL_KEY]
            LOGGER.info("Node pool name: %s", node_pool_name)

            node_price_collector: Collector[Price]

            if config.cloud_provider == "aws":
                session = aiobotocore.session.get_session()
                pricing_client = await exit_stack.enter_async_context(
                    session.create_client(
                        "pricing", get_aws_pricing_api_region(config.region)
                    )
                )
                ec2_client = await exit_stack.enter_async_context(
                    session.create_client("ec2", config.region)
                )
                node_price_collector = await exit_stack.enter_async_context(
                    AWSNodePriceCollector(
                        pricing_client=pricing_client,
                        ec2_client=ec2_client,
                        node_created_at=node.metadata.creation_timestamp,
                        region=config.region,
                        zone=zone,
                        instance_type=instance_type,
                        is_spot=is_preemptible,
                    )
                )
            elif config.cloud_provider == "gcp":
                assert config.gcp_service_account_key_path
                node_price_collector = await exit_stack.enter_async_context(
                    GCPNodePriceCollector(
                        cluster_holder=cluster_holder,
                        service_account_path=config.gcp_service_account_key_path,
                        node_pool_name=node_pool_name,
                        node_created_at=node.metadata.creation_timestamp,
                        region=config.region,
                        instance_type=instance_type,
                        is_preemptive=is_preemptible,
                    )
                )
            elif config.cloud_provider == "azure":
                prices_client = await exit_stack.enter_async_context(
                    aiohttp.ClientSession()
                )
                node_price_collector = await exit_stack.enter_async_context(
                    AzureNodePriceCollector(
                        prices_client=prices_client,
                        prices_url=config.azure_prices_url,
                        node_created_at=node.metadata.creation_timestamp,
                        region=config.region,
                        instance_type=instance_type,
                        is_spot=is_preemptible,
                    )
                )
            else:
                node_price_collector = await exit_stack.enter_async_context(
                    ConfigPriceCollector(
                        cluster_holder=cluster_holder,
                        node_created_at=node.metadata.creation_timestamp,
                        node_pool_name=node_pool_name,
                    )
                )
            app[NODE_PRICE_COLLECTOR_APP_KEY] = node_price_collector

            pod_credits_collector = await exit_stack.enter_async_context(
                PodCreditsCollector(
                    kube_client=kube_client,
                    cluster_holder=cluster_holder,
                    node_name=config.node_name,
                )
            )
            app[POD_CREDITS_COLLECTOR_APP_KEY] = pod_credits_collector

            node_power_consumpt_collector = await exit_stack.enter_async_context(
                NodeEnergyConsumptionCollector(
                    cluster_holder=cluster_holder,
                    node_pool_name=node_pool_name,
                )
            )
            app[NODE_POWER_CONSUMPTION_COLLECTOR_APP_KEY] = (
                node_power_consumpt_collector
            )

            await exit_stack.enter_async_context(
                run_task(await node_price_collector.start())
            )

            await exit_stack.enter_async_context(
                run_task(await pod_credits_collector.start())
            )

            await exit_stack.enter_async_context(
                run_task(await node_power_consumpt_collector.start())
            )

            yield

    app.cleanup_ctx.append(_init_app)

    return app


def create_prometheus_proxy_app(
    config: PrometheusProxyConfig,
) -> aiohttp.web.Application:
    app = aiohttp.web.Application()
    api_v1_app = aiohttp.web.Application(middlewares=[handle_exceptions])
    ProbesHandler(api_v1_app).register()
    PrometheusProxyHandler(api_v1_app).register()

    app.add_subapp("/api/v1", api_v1_app)

    async def _init_app(app: aiohttp.web.Application) -> AsyncIterator[None]:
        async with AsyncExitStack() as exit_stack:
            api_v1_app[PROMETHEUS_PROXY_APP_KEY] = config

            LOGGER.info("Initializing Auth client")
            auth_client = await exit_stack.enter_async_context(
                AuthClient(config.platform_auth.url, config.platform_auth.token)
            )

            LOGGER.info("Initializing Api client")
            api_client = await exit_stack.enter_async_context(
                create_api_client(config.platform_api)
            )

            auth_service = AuthService(auth_client, api_client, config.cluster_name)
            api_v1_app[AUTH_SERVICE_APP_KEY] = auth_service

            LOGGER.info("Initializing Prometheus client")
            prometheus_client = await exit_stack.enter_async_context(
                aiohttp.ClientSession(auto_decompress=False, timeout=config.timeout)
            )
            api_v1_app[PROMETHEUS_CLIENT_APP_KEY] = prometheus_client

            yield

    app.cleanup_ctx.append(_init_app)

    return app


def create_grafana_proxy_app(config: GrafanaProxyConfig) -> aiohttp.web.Application:
    app = aiohttp.web.Application(middlewares=[handle_exceptions])
    ProbesHandler(app).register()
    GrafanaProxyHandler(app).register()

    async def _init_app(app: aiohttp.web.Application) -> AsyncIterator[None]:
        async with AsyncExitStack() as exit_stack:
            app[GRAFANA_PROXY_APP_KEY] = config

            LOGGER.info("Initializing Auth client")
            auth_client = await exit_stack.enter_async_context(
                AuthClient(config.platform_auth.url, config.platform_auth.token)
            )

            LOGGER.info("Initializing Api client")
            api_client = await exit_stack.enter_async_context(
                create_api_client(config.platform_api)
            )

            auth_service = AuthService(auth_client, api_client, config.cluster_name)
            app[AUTH_SERVICE_APP_KEY] = auth_service

            LOGGER.info("Initializing Grafana client")
            grafana_client = await exit_stack.enter_async_context(
                aiohttp.ClientSession(auto_decompress=False, timeout=config.timeout)
            )
            app[GRAFANA_CLIENT_APP_KEY] = grafana_client

            yield

    app.cleanup_ctx.append(_init_app)

    app.on_response_prepare.append(add_version_to_header)

    return app


def create_metrics_api_app(config: MetricsApiConfig) -> aiohttp.web.Application:
    async def _init_app(app: aiohttp.web.Application) -> AsyncIterator[None]:
        async with AsyncExitStack() as exit_stack:
            app[METRICS_API_CONFIG_APP_KEY] = config

            LOGGER.info("Initializing Auth client")
            auth_client = await exit_stack.enter_async_context(
                AuthClient(config.platform.auth_yarl_url, config.platform.token)
            )
            await setup_security(app, auth_client)

            LOGGER.info("Initializing Config client")
            config_client = await exit_stack.enter_async_context(
                ConfigClient(
                    config.platform.config_yarl_url, token=config.platform.token
                )
            )
            cluster_holder = await exit_stack.enter_async_context(
                RefreshableClusterHolder(
                    config_client=config_client, cluster_name=config.cluster_name
                )
            )

            LOGGER.info("Initializing Prometheus client")
            raw_client = await exit_stack.enter_async_context(aiohttp.ClientSession())
            prometheus_client = PrometheusClient(
                client=raw_client, prometheus_url=config.prometheus_yarl_url
            )

            LOGGER.info("Initializing Metrics service")
            metrics_service = MetricsService(
                prometheus_client=prometheus_client, cluster_holder=cluster_holder
            )
            app[METRICS_SERVICE_APP_KEY] = metrics_service

            yield

    app = aiohttp.web.Application(
        middlewares=[handle_exceptions, validation_middleware]
    )
    app.on_response_prepare.append(add_version_to_header)
    ProbesHandler(app).register()

    metrics_app = aiohttp.web.Application()
    metrics_app.cleanup_ctx.append(_init_app)
    MetricsApiHandler(metrics_app).register()

    app.add_subapp("/api", metrics_app)

    prefix = "/api/metrics/docs"
    setup_aiohttp_apispec(
        app=app,
        title="Metrics API documentation",
        url=f"{prefix}/swagger.json",
        static_path=f"{prefix}/static",
        swagger_path=prefix,
        security=[{"bearerAuth": []}],
        securityDefinitions={
            "bearerAuth": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": (
                    "Enter the token with the `Bearer: ` prefix, "
                    'e.g. "Bearer <token>".'
                ),
            },
        },
    )

    return app


def run_metrics_exporter() -> None:  # pragma: no coverage
    init_logging(health_check_url_path="/ping")
    config = EnvironConfigFactory().create_metrics()
    logging.info("Loaded config: %r", config)
    setup_sentry(health_check_url_path="/ping")
    loop = uvloop.new_event_loop()
    aiohttp.web.run_app(
        create_metrics_exporter_app(config),
        host=config.server.host,
        port=config.server.port,
        loop=loop,
    )


def run_prometheus_proxy() -> None:  # pragma: no coverage
    init_logging()
    config = EnvironConfigFactory().create_prometheus_proxy()
    logging.info("Loaded config: %r", config)
    setup_sentry()
    loop = uvloop.new_event_loop()
    aiohttp.web.run_app(
        create_prometheus_proxy_app(config),
        host=config.server.host,
        port=config.server.port,
        handler_cancellation=True,
        loop=loop,
    )


def run_grafana_proxy() -> None:  # pragma: no coverage
    init_logging(health_check_url_path="/ping")
    config = EnvironConfigFactory().create_grafana_proxy()
    logging.info("Loaded config: %r", config)
    setup_sentry(health_check_url_path="/ping")
    loop = uvloop.new_event_loop()
    aiohttp.web.run_app(
        create_grafana_proxy_app(config),
        host=config.server.host,
        port=config.server.port,
        handler_cancellation=True,
        loop=loop,
    )


def run_metrics_api() -> None:  # pragma: no coverage
    init_logging(health_check_url_path="/ping")
    config = MetricsApiConfig()  # type: ignore
    logging.info("Loaded config: %r", config)
    setup_sentry(health_check_url_path="/ping")
    loop = uvloop.new_event_loop()
    aiohttp.web.run_app(
        create_metrics_api_app(config),
        host=config.server.host,
        port=config.server.port,
        handler_cancellation=True,
        loop=loop,
    )
