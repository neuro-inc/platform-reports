import asyncio
import copy
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from tempfile import mktemp
from typing import Any, AsyncIterator, Awaitable, Callable, Dict

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
from platform_logging import DEFAULT_CONFIG, init_logging

from .auth import AuthService
from .config import (
    EnvironConfigFactory,
    GrafanaProxyConfig,
    MetricsConfig,
    PlatformApiConfig,
    PrometheusProxyConfig,
    ServerConfig,
)
from .metrics import PriceCollector


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
    def _node_price_collector(self) -> PriceCollector:
        return self._app["node_price_collector"]

    @property
    def _config(self) -> MetricsConfig:
        return self._app["config"]

    async def handle(self, request: Request) -> Response:
        node_price_per_hour = self._node_price_collector.current_price_per_hour
        # https://prometheus.io/docs/instrumenting/exposition_formats/#text-based-format
        return Response(
            text=f"""\
# HELP node_price_per_hour The price of the node per hour.
# TYPE node_price_per_hour gauge
node_price_per_hour{{\
node="{self._config.host_name}",\
instance_type="{self._config.instance_type}",\
currency="{node_price_per_hour.currency}"\
}} {node_price_per_hour.value}"""
        )


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
            proxy_server=self._config.server,
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
            proxy_server=self._config.public_server,
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
            proxy_server=self._config.public_server,
            upstream_server=self._config.grafana_server,
            request=request,
        )


def _get_user_name(request: Request, access_token_cookie_name: str) -> str:
    access_token = request.cookies[access_token_cookie_name]
    claims = jwt.get_unverified_claims(access_token)
    return claims["https://platform.neuromation.io/user"]


async def _proxy_request(
    client: aiohttp.ClientSession,
    proxy_server: ServerConfig,
    upstream_server: ServerConfig,
    request: Request,
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
async def create_api_client(config: PlatformApiConfig) -> AsyncIterator[ApiClient]:
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
            node_price_collector = PriceCollector()
            app["node_price_collector"] = node_price_collector

            await exit_stack.enter_async_context(
                run_task(await node_price_collector.start())
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
