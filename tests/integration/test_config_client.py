from neuro_config_client import ConfigClient

from platform_reports.config_client import ClusterListener


class TestClusterListener:
    async def test(self, config_client: ConfigClient) -> None:
        listener = ClusterListener(config_client, "default")

        await listener.start()

        assert listener.cluster.name == "default"

        await listener.stop()
