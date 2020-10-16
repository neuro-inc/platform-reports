import aiohttp
import pytest

from platform_reports.kube_client import KubeClient, PodPhase


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

    @pytest.mark.asyncio
    async def test_get_pods(self, kube_client: KubeClient) -> None:
        result = await kube_client.get_pods(namespace="kube-system")

        assert result

    @pytest.mark.asyncio
    async def test_get_pods_with_label_selector(self, kube_client: KubeClient) -> None:
        pods = await kube_client.get_pods(
            namespace="kube-system", label_selector="k8s-app=kube-proxy"
        )

        assert pods

        for pod in pods:
            assert pod.metadata.name.startswith(
                "kube-proxy"
            ), f"Found pod {pod.metadata.name}"

    @pytest.mark.asyncio
    async def test_get_pods_with_field_selector(self, kube_client: KubeClient) -> None:
        pods = await kube_client.get_pods(
            namespace="kube-system",
            field_selector=(
                "spec.nodeName=minikube,status.phase!=Failed,status.phase!=Succeeded"
            ),
        )

        assert pods

        for pod in pods:
            assert pod.status.phase in (
                PodPhase.PENDING,
                PodPhase.RUNNING,
            ), f"Pod {pod.metadata.name} is in {pod.status.phase.value} phase"

    @pytest.mark.asyncio
    async def test_get_pods_in_unknown_namespace(self, kube_client: KubeClient) -> None:
        result = await kube_client.get_pods(namespace="unknown")

        assert result == []
