from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import aiohttp
import pytest
from jose import jwt
from neuro_auth_client import AuthClient, Permission, User as AuthUser
from yarl import URL


_JWT_SECRET = "secret"


@pytest.fixture()
def token_factory() -> Callable[[str], str]:
    def _create(name: str) -> str:
        payload = {"https://platform.neuromation.io/user": name}
        return jwt.encode(payload, _JWT_SECRET, algorithm="HS256")

    return _create


@dataclass(frozen=True)
class User(AuthUser):
    token: str


UserFactory = Callable[[str, Sequence[Permission]], Awaitable[User]]


@pytest.fixture()
def user_factory(
    token_factory: Callable[[str], str], platform_auth_server: URL
) -> UserFactory:
    async def _create(name: str, permissions: Sequence[Permission] = ()) -> User:
        async with AuthClient(
            url=platform_auth_server, token=token_factory("admin")
        ) as client:
            try:
                await client.add_user(AuthUser(name=name))
            except aiohttp.ClientResponseError as ex:
                if "already exists" not in ex.message:
                    raise
            if permissions:
                await client.grant_user_permissions(name, permissions)
            return User(name=name, token=token_factory(name))

    return _create


@pytest.fixture()
async def service_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory("cluster", [Permission(uri="user://", action="read")])
    return token_factory("cluster")


@pytest.fixture()
async def cluster_admin_token(user_factory: UserFactory) -> str:
    user = await user_factory(
        "cluster-admin",
        [
            Permission(uri="role://default/manager", action="manage"),
            Permission(uri="cluster://default/access", action="read"),
        ],
    )
    return user.token


@pytest.fixture()
async def regular_user_token(user_factory: UserFactory) -> str:
    user = await user_factory(
        "user",
        [
            Permission(uri="cluster://default/access", action="read"),
            Permission(uri="job://default/user", action="manage"),
        ],
    )
    return user.token


@pytest.fixture()
async def other_cluster_user_token(user_factory: UserFactory) -> str:
    user = await user_factory(
        "other-user",
        [
            Permission(uri="cluster://neuro-public/access", action="read"),
            Permission(uri="job://neuro-public/other-user", action="manage"),
        ],
    )
    return user.token
