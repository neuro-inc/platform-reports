from typing import Any, Callable, Coroutine, Sequence

import aiohttp
import pytest
from jose import jwt
from neuro_auth_client import AuthClient, Cluster, Permission, User
from yarl import URL


_JWT_SECRET = "secret"


@pytest.fixture
def token_factory() -> Callable[[str], str]:
    def _create(name: str) -> str:
        payload = {"https://platform.neuromation.io/user": name}
        return jwt.encode(payload, _JWT_SECRET, algorithm="HS256")

    return _create


@pytest.fixture
def user_factory(
    token_factory: Callable[[str], str], platform_auth_server: URL
) -> Callable[
    [str, Sequence[Cluster], Sequence[Permission]], Coroutine[Any, Any, None]
]:
    async def _create(
        name: str, clusters: Sequence[Cluster], permissions: Sequence[Permission] = ()
    ) -> None:
        async with AuthClient(
            url=platform_auth_server, token=token_factory("admin")
        ) as client:
            try:
                await client.add_user(User(name=name, clusters=list(clusters)))
            except aiohttp.ClientResponseError as ex:
                if "already exists" not in ex.message:
                    raise
            if permissions:
                await client.grant_user_permissions(name, permissions)

    return _create
