import asyncio
import copy
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from tempfile import mktemp
from textwrap import dedent
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Mapping

import aiobotocore
import aiohttp
import aiohttp.web
from aiohttp.web import (
    HTTPBadRequest,
    HTTPForbidden,
    HTTPInternalServerError,
    Request,
    Response,
    StreamResponse,
    json_response,
    middleware,
)
from jose import jwt
from multidict import CIMultiDict, CIMultiDictProxy
from neuro_auth_client import AuthClient, Permission
from neuromation.api import Client as ApiClient, Factory as ClientFactory
from platform_config_client.client import ConfigClient
from platform_logging import DEFAULT_CONFIG, init_logging

from .auth import AuthService
from .config import (
    EnvironConfigFactory,
    GrafanaProxyConfig,
    MetricsConfig,
    PlatformServiceConfig,
    PrometheusProxyConfig,
    ServerConfig,
)
from .kube_client import KubeClient
from .metrics import (
    AWSNodePriceCollector,
    Collector,
    GCPNodePriceCollector,
    PodPriceCollector,
    Price,
)


logger = logging.getLogger(__name__)


class ProbesHandler:
    def __init__(self, app: aiohttp.web.Application) -> None:
        self._app = app

    def register(self) -> None:
        self._app.router.add_get("/ping", self.handle_ping)

    async def handle_ping(self, request: Request) -> Response:
        return Response(text="Pong")


class MetricsHandler:
    def __init__(self, app: aiohttp.web.Application) -> None:
        self._app = app

    def register(self) -> None:
        self._app.router.add_get("/metrics", self.handle)

    @property
    def _node_price_collector(self) -> Collector[Price]:
        return self._app["node_price_collector"]

    @property
    def _pod_price_collector(self) -> Collector[Mapping[str, Price]]:
        return self._app["pod_price_collector"]

    @property
    def _config(self) -> MetricsConfig:
        return self._app["config"]

    async def handle(self, request: Request) -> Response:
        text = [self._get_node_price_per_hour_text()]
        pod_prices_per_hour_text = self._get_pod_prices_per_hour_text()
        if pod_prices_per_hour_text:
            text.append(pod_prices_per_hour_text)
        return Response(text="\n\n".join(text))

    def _get_node_price_per_hour_text(self) -> str:
        node = self._config.node_name
        price = self._node_price_collector.current_value
        return dedent(
            f"""\
            # HELP kube_node_price_per_hour The price of the node per hour.
            # TYPE kube_node_price_per_hour gauge
            kube_node_price_per_hour{{node="{node}",currency="{price.currency}"}} {price.value}"""  # noqa: E501
        )

    def _get_pod_prices_per_hour_text(self) -> str:
        pod_prices_per_hour = self._pod_price_collector.current_value
        if not pod_prices_per_hour:
            return ""
        metrics: List[str] = [
            dedent(
                """\
                # HELP kube_pod_price_per_hour The price of the pod per hour.
                # TYPE kube_pod_price_per_hour gauge"""
            )
        ]
        for name, price in pod_prices_per_hour.items():
            metrics.append(
                f'kube_pod_price_per_hour{{pod="{name}",currency="{price.currency}"}} {price.value}'  # noqa: E501
            )
        return "\n".join(metrics)


class PrometheusProxyHandler:
    def __init__(self, app: aiohttp.web.Application) -> None:
        self._app = app

    def register(self) -> None:
        self._app.router.add_get("/{sub_path:.*}", self.handle)

    @property
    def _config(self) -> PrometheusProxyConfig:
        return self._app["config"]

    @property
    def _prometheus_client(self) -> aiohttp.ClientSession:
        return self._app["prometheus_client"]

    @property
    def _auth_service(self) -> AuthService:
        return self._app["auth_service"]

    async def handle(self, request: Request) -> StreamResponse:
        user_name = _get_user_name(request, self._config.access_token_cookie_name)

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
            # to access any data inside in prometheus.
            if not await self._auth_service.check_permissions(
                user_name,
                [
                    Permission(
                        uri=(
                            f"cluster://{self._config.cluster_name}"
                            "/admin/cloud_provider/infra"
                        ),
                        action="read",
                    ),
                    Permission(
                        uri=f"job://{self._config.cluster_name}", action="read",
                    ),
                ],
            ):
                return Response(status=HTTPForbidden.status_code)

        return await _proxy_request(
            client=self._prometheus_client,
            upstream_server=self._config.prometheus_server,
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
        return self._app["config"]

    @property
    def _grafana_client(self) -> aiohttp.ClientSession:
        return self._app["grafana_client"]

    @property
    def _auth_service(self) -> AuthService:
        return self._app["auth_service"]

    async def handle(self, request: Request) -> StreamResponse:
        user_name = _get_user_name(request, self._config.access_token_cookie_name)

        # Check that user has access to his own jobs in cluster.
        if not await self._auth_service.check_permissions(
            user_name,
            [
                Permission(
                    uri=(f"job://{self._config.cluster_name}/{user_name}"),
                    action="read",
                )
            ],
        ):
            return Response(status=HTTPForbidden.status_code)

        return await _proxy_request(
            client=self._grafana_client,
            upstream_server=self._config.grafana_server,
            request=request,
        )

    async def handle_get_dashboard(self, request: Request) -> StreamResponse:
        user_name = _get_user_name(request, self._config.access_token_cookie_name)
        dashboard_id = request.match_info["dashboard_id"]

        if not await self._auth_service.check_dashboard_permissions(
            user_name=user_name, dashboard_id=dashboard_id, params=request.query
        ):
            return Response(status=HTTPForbidden.status_code)

        return await _proxy_request(
            client=self._grafana_client,
            upstream_server=self._config.grafana_server,
            request=request,
        )


def _get_user_name(request: Request, access_token_cookie_name: str) -> str:
    access_token = request.cookies[access_token_cookie_name]
    claims = jwt.get_unverified_claims(access_token)
    return claims["https://platform.neuromation.io/user"]


async def _proxy_request(
    client: aiohttp.ClientSession, upstream_server: ServerConfig, request: Request,
) -> StreamResponse:
    upstream_url = (
        request.url.with_scheme(upstream_server.scheme)
        .with_host(upstream_server.host)
        .with_port(upstream_server.port)
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
        logger.debug("upstream response: %s", upstream_response)

        response = aiohttp.web.StreamResponse(
            status=upstream_response.status, headers=upstream_response.headers.copy()
        )

        await response.prepare(request)

        logger.debug("response: %s; headers: %s", response, response.headers)

        async for chunk in upstream_response.content.iter_any():
            await response.write(chunk)

        await response.write_eof()
        return response


def _prepare_upstream_request_headers(
    headers: CIMultiDictProxy[str],
) -> CIMultiDict[str]:
    request_headers: CIMultiDict[str] = headers.copy()

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
    except Exception as e:
        msg_str = (
            f"Unexpected exception: {str(e)}. " f"Path with query: {request.path_qs}."
        )
        logging.exception(msg_str)
        payload = {"error": msg_str}
        return json_response(payload, status=HTTPInternalServerError.status_code)


@asynccontextmanager
async def run_task(coro: Awaitable[None]) -> AsyncIterator[None]:
    task = asyncio.create_task(coro)
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@asynccontextmanager
async def create_api_client(config: PlatformServiceConfig) -> AsyncIterator[ApiClient]:
    tmp_config = Path(mktemp())
    platform_api_factory = ClientFactory(tmp_config)
    await platform_api_factory.login_with_token(url=config.url, token=config.token)
    client = None
    try:
        client = await platform_api_factory.get()
        yield client
    finally:
        if client:
            await client.close()


def create_metrics_app(config: MetricsConfig) -> aiohttp.web.Application:
    app = aiohttp.web.Application()
    ProbesHandler(app).register()
    MetricsHandler(app).register()

    app["config"] = config

    async def _init_app(app: aiohttp.web.Application) -> AsyncIterator[None]:
        async with AsyncExitStack() as exit_stack:
            config_client = await exit_stack.enter_async_context(
                ConfigClient(config.platform_config.url, config.platform_config.token)
            )

            kube_client = await exit_stack.enter_async_context(KubeClient(config.kube))
            node = await kube_client.get_node(config.node_name)
            instance_type = (
                node.metadata.labels.get("node.kubernetes.io/instance-type")
                or node.metadata.labels.get("beta.kubernetes.io/instance-type")
                or ""
            )
            app["instance_type"] = instance_type
            logger.info("Node instance type is %s", instance_type)

            preemptible = node.metadata.labels.get(config.node_preemptible_label, "")
            is_preemptible = preemptible.lower() == "true"
            if is_preemptible:
                logger.info("Node is preemptible")
            else:
                logger.info("Node is not preemptible")

            node_pool_name = node.metadata.labels.get(config.node_pool_label, "")
            app["node_pool_name"] = node_pool_name
            logger.info("Node pool name is %s", node_pool_name)

            if config.cloud_provider == "aws":
                assert config.region
                assert instance_type
                session = aiobotocore.get_session()
                pricing_client = await exit_stack.enter_async_context(
                    session.create_client("pricing", config.region)
                )
                node_price_collector = await exit_stack.enter_async_context(
                    AWSNodePriceCollector(
                        pricing_client=pricing_client,
                        region=config.region,
                        instance_type=instance_type,
                    )
                )
            elif config.cloud_provider == "gcp":
                assert config.region
                assert config.gcp_service_account_key_path
                assert instance_type
                node_price_collector = await exit_stack.enter_async_context(
                    GCPNodePriceCollector(
                        config_client=config_client,
                        service_account_path=config.gcp_service_account_key_path,
                        cluster_name=config.cluster_name,
                        node_pool_name=node_pool_name,
                        region=config.region,
                        instance_type=instance_type,
                        is_preemptible=is_preemptible,
                    )
                )
            else:
                node_price_collector = Collector(Price())
            app["node_price_collector"] = node_price_collector

            pod_price_collector = await exit_stack.enter_async_context(
                PodPriceCollector(
                    config_client=config_client,
                    kube_client=kube_client,
                    node_price_collector=node_price_collector,
                    cluster_name=config.cluster_name,
                    node_name=config.node_name,
                    node_pool_name=node_pool_name,
                    jobs_namespace=config.jobs_namespace,
                    job_label=config.job_label,
                )
            )
            app["pod_price_collector"] = pod_price_collector

            await exit_stack.enter_async_context(
                run_task(await node_price_collector.start())
            )

            await exit_stack.enter_async_context(
                run_task(await pod_price_collector.start())
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
            api_v1_app["config"] = config

            logger.info("Initializing Auth client")
            auth_client = await exit_stack.enter_async_context(
                AuthClient(config.platform_auth.url, config.platform_auth.token)
            )
            api_v1_app["auth_client"] = auth_client

            logger.info("Initializing Api client")
            api_client = await exit_stack.enter_async_context(
                create_api_client(config.platform_api)
            )
            api_v1_app["api_client"] = auth_client

            auth_service = AuthService(auth_client, api_client, config.cluster_name)
            api_v1_app["auth_service"] = auth_service

            logger.info("Initializing Prometheus client")
            prometheus_client = await exit_stack.enter_async_context(
                aiohttp.ClientSession(auto_decompress=False, timeout=config.timeout)
            )
            api_v1_app["prometheus_client"] = prometheus_client

            yield

    app.cleanup_ctx.append(_init_app)

    return app


def create_grafana_proxy_app(config: GrafanaProxyConfig) -> aiohttp.web.Application:
    app = aiohttp.web.Application(middlewares=[handle_exceptions])
    ProbesHandler(app).register()
    GrafanaProxyHandler(app).register()

    async def _init_app(app: aiohttp.web.Application) -> AsyncIterator[None]:
        async with AsyncExitStack() as exit_stack:
            app["config"] = config

            logger.info("Initializing Auth client")
            auth_client = await exit_stack.enter_async_context(
                AuthClient(config.platform_auth.url, config.platform_auth.token)
            )
            app["auth_client"] = auth_client

            logger.info("Initializing Api client")
            api_client = await exit_stack.enter_async_context(
                create_api_client(config.platform_api)
            )
            app["api_client"] = auth_client

            auth_service = AuthService(auth_client, api_client, config.cluster_name)
            app["auth_service"] = auth_service

            logger.info("Initializing Grafana client")
            grafana_client = await exit_stack.enter_async_context(
                aiohttp.ClientSession(auto_decompress=False, timeout=config.timeout)
            )
            app["grafana_client"] = grafana_client

            yield

    app.cleanup_ctx.append(_init_app)

    return app


def create_logging_config() -> Dict[str, Any]:
    config: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
    config["root"]["level"] = os.environ.get("NP_LOG_LEVEL", "INFO")
    return config


def run_metrics_server() -> None:  # pragma: no coverage
    init_logging(create_logging_config())

    config = EnvironConfigFactory().create_metrics()
    logging.info("Loaded config: %r", config)
    aiohttp.web.run_app(
        create_metrics_app(config), host=config.server.host, port=config.server.port,
    )


def run_prometheus_proxy() -> None:  # pragma: no coverage
    init_logging(create_logging_config())

    config = EnvironConfigFactory().create_prometheus_proxy()
    logging.info("Loaded config: %r", config)
    aiohttp.web.run_app(
        create_prometheus_proxy_app(config),
        host=config.server.host,
        port=config.server.port,
    )


def run_grafana_proxy() -> None:  # pragma: no coverage
    init_logging(create_logging_config())

    config = EnvironConfigFactory().create_grafana_proxy()
    logging.info("Loaded config: %r", config)
    aiohttp.web.run_app(
        create_grafana_proxy_app(config),
        host=config.server.host,
        port=config.server.port,
    )
