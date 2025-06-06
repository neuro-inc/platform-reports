from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import aiohttp
import pytest
from jose import jwt
from neuro_auth_client import AuthClient, Permission, User as AuthUser
from pytest_docker.plugin import Services
from yarl import URL


LOGGER = logging.getLogger(__name__)


_JWT_SECRET = "secret"


@pytest.fixture
async def platform_auth_server(docker_ip: str, docker_services: Services) -> URL:
    port = docker_services.port_for("platform-auth", 8080)
    url = URL(f"http://{docker_ip}:{port}")
    await wait_for_auth_server(url)
    return url


async def wait_for_auth_server(
    url: URL, timeout_s: float = 300, interval_s: float = 1
) -> None:
    async def _wait() -> None:
        last_exc = None
        try:
            while True:
                try:
                    async with AuthClient(url=url, token="") as auth_client:
                        await auth_client.ping()
                        break
                except (AssertionError, OSError, aiohttp.ClientError) as exc:
                    last_exc = exc
                LOGGER.debug("waiting for %s: %s", url, last_exc)
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            pytest.fail(f"failed to connect to {url}: {last_exc}")

    await asyncio.wait_for(_wait(), timeout=timeout_s)


@pytest.fixture
def token_factory() -> Callable[[str], str]:
    def _create(name: str) -> str:
        payload = {"https://platform.neuromation.io/user": name}
        return jwt.encode(payload, _JWT_SECRET, algorithm="HS256")

    return _create


@dataclass(frozen=True)
class User(AuthUser):
    token: str


UserFactory = Callable[[str, Sequence[Permission]], Awaitable[User]]


@pytest.fixture
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


@pytest.fixture
async def service_token(
    user_factory: UserFactory, token_factory: Callable[[str], str]
) -> str:
    await user_factory("cluster", [Permission(uri="user://", action="read")])
    return token_factory("cluster")


@pytest.fixture
async def cluster_admin_token(user_factory: UserFactory) -> str:
    user = await user_factory(
        "cluster-admin",
        [
            Permission(uri="role://default/manager", action="manage"),
            Permission(uri="cluster://default/access", action="read"),
        ],
    )
    return user.token


@pytest.fixture
async def regular_user_token(user_factory: UserFactory) -> str:
    user = await user_factory(
        "user",
        [
            Permission(uri="cluster://default/access", action="read"),
            Permission(uri="job://default/user", action="manage"),
        ],
    )
    return user.token


@pytest.fixture
async def other_cluster_user_token(user_factory: UserFactory) -> str:
    user = await user_factory(
        "other-user",
        [
            Permission(uri="cluster://neuro-public/access", action="read"),
            Permission(uri="job://neuro-public/other-user", action="manage"),
        ],
    )
    return user.token
