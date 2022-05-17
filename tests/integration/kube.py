from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from yarl import URL

from platform_reports.config import KubeClientAuthType, KubeConfig
from platform_reports.kube_client import Container, KubeClient, Metadata, Pod, Resources


class MyKubeClient(KubeClient):
    async def create_pod(self, namespace: str, pod: Pod) -> Pod:
        assert self._client
        payload = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": pod.metadata.name},
            "spec": {
                "containers": [
                    {
                        "name": c.name,
                        "image": c.image,
                        "resources": {
                            "requests": {
                                "cpu": f"{c.resource_requests.cpu_m}m",
                                "memory": f"{c.resource_requests.memory_mb}Mi",
                            }
                        },
                    }
                    for c in pod.containers
                ]
            },
        }
        async with self._client.post(
            self._get_pods_url(namespace), json=payload
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()
            self._raise_for_status(payload)
            return Pod.from_payload(payload)

    async def delete_pod(self, namespace: str, name: str) -> None:
        assert self._client
        async with self._client.delete(self._get_pods_url(namespace) / name) as resp:
            resp.raise_for_status()


@pytest.fixture(scope="session")
def _kube_config_payload() -> dict[str, Any]:
    kube_config_path = os.path.expanduser("~/.kube/config")
    with open(kube_config_path) as kube_config:
        return yaml.safe_load(kube_config)


@pytest.fixture(scope="session")
def _kube_config_cluster_payload(
    _kube_config_payload: dict[str, Any]
) -> dict[str, Any]:
    clusters = {
        cluster["name"]: cluster["cluster"]
        for cluster in _kube_config_payload["clusters"]
    }
    return clusters["minikube"]


@pytest.fixture(scope="session")
def _kube_config_user_payload(_kube_config_payload: dict[str, Any]) -> dict[str, Any]:
    users = {user["name"]: user["user"] for user in _kube_config_payload["users"]}
    return users["minikube"]


@pytest.fixture(scope="session")
def _cert_authority_data_pem(_kube_config_cluster_payload: dict[str, Any]) -> str:
    if "certificate-authority" in _kube_config_cluster_payload:
        return Path(_kube_config_cluster_payload["certificate-authority"]).read_text()
    return _kube_config_cluster_payload["certificate-authority-data"]


@pytest.fixture(scope="session")
def kube_config(
    _kube_config_cluster_payload: dict[str, Any],
    _kube_config_user_payload: dict[str, Any],
    _cert_authority_data_pem: str | None,
) -> KubeConfig:
    return KubeConfig(
        url=URL(_kube_config_cluster_payload["server"]),
        auth_type=KubeClientAuthType.CERTIFICATE,
        cert_authority_data_pem=_cert_authority_data_pem,
        client_cert_path=_kube_config_user_payload["client-certificate"],
        client_key_path=_kube_config_user_payload["client-key"],
    )


@pytest.fixture
async def kube_client(kube_config: KubeConfig) -> AsyncIterator[KubeClient]:
    async with MyKubeClient(kube_config) as client:
        yield client


@pytest.fixture
async def pod_factory(
    kube_client: MyKubeClient,
) -> AsyncIterator[Callable[..., Awaitable[Pod]]]:
    pods: list[Pod] = []

    async def _create(image: str, cpu_m: int = 100, memory_mb: int = 64) -> Pod:
        pod = Pod(
            metadata=Metadata(name=str(uuid.uuid4())),
            status=None,  # type: ignore
            containers=[
                Container(
                    name="test",
                    image=image,
                    resource_requests=Resources(cpu_m=cpu_m, memory_mb=memory_mb),
                )
            ],
        )
        pod = await kube_client.create_pod("default", pod)
        pods.append(pod)
        return pod

    yield _create

    for pod in pods:
        await kube_client.delete_pod("default", pod.metadata.name)
