import logging
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Any, Dict, Optional, Type

import aiohttp

from .config import KubeClientAuthType, KubeConfig


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Metadata:
    name: str
    labels: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Metadata":
        return cls(name=payload["name"], labels=payload.get("labels", {}))


@dataclass(frozen=True)
class Node:
    metadata: Metadata

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Node":
        return cls(metadata=Metadata.from_payload(payload["metadata"]))


class KubeClient:
    def __init__(self, config: KubeConfig) -> None:
        self._config = config
        self._client: Optional[aiohttp.ClientSession] = None

    async def _on_request_start(
        self,
        session: aiohttp.ClientSession,
        trace_config_ctx: SimpleNamespace,
        params: aiohttp.TraceRequestStartParams,
    ) -> None:
        logger.info("Sending %s %s", params.method, params.url)

    async def _on_request_end(
        self,
        session: aiohttp.ClientSession,
        trace_config_ctx: SimpleNamespace,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        default_args = (params.method, params.response.status, params.url)
        if 400 <= params.response.status:
            response_text = await params.response.text()
            logger.warning("Received %s %s %s\n%s", *default_args, response_text)
        else:
            logger.info("Received %s %s %s", *default_args)

    def _create_ssl_context(self) -> Optional[ssl.SSLContext]:
        if self._config.url.scheme != "https":
            return None
        ssl_context = ssl.create_default_context(
            cafile=self._config.cert_authority_path,
            cadata=self._config.cert_authority_data_pem,
        )
        if self._config.auth_type == KubeClientAuthType.CERTIFICATE:
            ssl_context.load_cert_chain(
                self._config.auth_cert_path,  # type: ignore
                self._config.auth_cert_key_path,
            )
        return ssl_context

    async def __aenter__(self) -> "KubeClient":
        self._client = await self.create_http_client()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        await self.aclose()

    async def create_http_client(
        self, *, force_close: bool = False
    ) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(
            limit=self._config.conn_pool_size,
            ssl=self._create_ssl_context(),
            force_close=force_close,
        )
        if self._config.auth_type == KubeClientAuthType.TOKEN:
            token = self._config.token
            if not token:
                assert self._config.token_path is not None
                token = Path(self._config.token_path).read_text()
            headers = {"Authorization": "Bearer " + token}
        else:
            headers = {}
        timeout = aiohttp.ClientTimeout(
            connect=self._config.conn_timeout_s, total=self._config.read_timeout_s
        )
        trace_config = aiohttp.TraceConfig()
        trace_config.on_request_start.append(self._on_request_start)  # type: ignore
        trace_config.on_request_end.append(self._on_request_end)  # type: ignore
        return aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=headers,
            trace_configs=[trace_config],
        )

    async def aclose(self) -> None:
        assert self._client
        await self._client.close()

    async def get_node(self, name: str) -> Node:
        assert self._client
        async with self._client.get(
            self._config.url / "api/v1/nodes" / name
        ) as response:
            response.raise_for_status()
            payload = await response.json()
            assert payload["kind"] == "Node"
            return Node.from_payload(payload)
