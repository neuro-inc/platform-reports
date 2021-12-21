from __future__ import annotations

from collections.abc import AsyncIterator
import os
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml
from yarl import URL

from platform_reports.config import KubeClientAuthType, KubeConfig
from platform_reports.kube_client import KubeClient


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
    async with KubeClient(kube_config) as client:
        yield client
