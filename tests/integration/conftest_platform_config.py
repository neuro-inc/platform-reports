from datetime import datetime

import aiohttp.web
import pytest


@pytest.fixture
def platform_config_app() -> aiohttp.web.Application:
    async def _get_cluster(request: aiohttp.web.Request) -> aiohttp.web.Response:
        name = request.match_info["cluster_name"]
        return aiohttp.web.json_response(
            {
                "name": name,
                "status": "deployed",
                "created_at": datetime.now().isoformat(),
                "orchestrator": {
                    "job_hostname_template": f"{{job_id}}.jobs.{name}.org.neu.ro",
                    "job_fallback_hostname": "default.jobs-dev.neu.ro",
                    "is_http_ingress_secure": False,
                    "job_schedule_timeout_s": 30,
                    "job_schedule_scale_up_timeout_s": 30,
                    "allow_privileged_mode": False,
                    "resource_pool_types": [
                        {
                            "name": "minikube-node-pool",
                            "min_size": 1,
                            "max_size": 1,
                            "cpu": 1,
                            "available_cpu": 1,
                            "memory": 4096 * 2**20,
                            "available_memory": 4096 * 2**20,
                            "price": "0.0",
                            "currency": "USD",
                            "cpu_min_watts": 1,
                            "cpu_max_watts": 10,
                            "co2_grams_eq_per_kwh": 0.5,
                        }
                    ],
                    "resource_presets": [
                        {
                            "name": "test-preset",
                            "cpu": 1,
                            "memory": 1024**3,
                            "credits_per_hour": "3600",
                        }
                    ],
                },
                "storage": {"url": f"https://{name}.org.neu.ro/api/v1/storage"},
                "blob_storage": {"url": f"https://{name}.org.neu.ro/api/v1/blob"},
                "registry": {"url": f"https://registry.{name}.org.neu.ro"},
                "monitoring": {"url": f"https://{name}.org.neu.ro/api/v1/jobs"},
                "secrets": {"url": f"https://{name}.org.neu.ro/api/v1/secrets"},
                "metrics": {"url": f"https://metrics.{name}.org.neu.ro"},
            }
        )

    app = aiohttp.web.Application()
    app.router.add_get("/api/v1/clusters/{cluster_name}", _get_cluster)
    return app
