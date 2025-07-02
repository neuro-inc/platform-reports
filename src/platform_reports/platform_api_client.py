from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType

import aiohttp
from yarl import URL


@dataclass(frozen=True)
class Job:
    id: str
    org_name: str
    project_name: str


class ApiClient:
    _client: aiohttp.ClientSession

    def __init__(
        self,
        url: URL,
        token: str | None = None,
        timeout: aiohttp.ClientTimeout = aiohttp.client.DEFAULT_TIMEOUT,
        trace_configs: Sequence[aiohttp.TraceConfig] = (),
    ):
        super().__init__()

        self._base_url = url / "api/v1"
        self._token = token
        self._timeout = timeout
        self._trace_configs = trace_configs

    async def __aenter__(self) -> "ApiClient":
        self._client = self._create_http_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _create_http_client(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            headers=self._create_default_headers(),
            timeout=self._timeout,
            trace_configs=list(self._trace_configs),
        )

    async def aclose(self) -> None:
        assert self._client
        await self._client.close()

    def _create_default_headers(self) -> dict[str, str]:
        result = {}
        if self._token:
            result["Authorization"] = f"Bearer {self._token}"
        return result

    async def get_job(self, id_: str) -> Job:
        async with self._client.get(self._base_url / "jobs" / id_) as response:
            response.raise_for_status()
            response_json = await response.json()
            return Job(
                id=response_json["id"],
                org_name=response_json["org_name"],
                project_name=response_json["project_name"],
            )
