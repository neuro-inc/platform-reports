import aiohttp
import pytest

from platform_reports.kube_client import KubeClient


class TestKubeClient:
    @pytest.mark.asyncio
    async def test_get_node(self, kube_client: KubeClient) -> None:
        node = await kube_client.get_node("minikube")

        assert node.metadata.name == "minikube"
        assert node.metadata.labels

    @pytest.mark.asyncio
    async def test_get_unknown_node__raises_error(
        self, kube_client: KubeClient
    ) -> None:
        with pytest.raises(aiohttp.ClientError):
            await kube_client.get_node("unknown")
