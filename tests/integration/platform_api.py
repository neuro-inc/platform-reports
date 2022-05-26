import aiohttp.web
import pytest


@pytest.fixture
def platform_api_app() -> aiohttp.web.Application:
    async def _get_config(request: aiohttp.web.Request) -> aiohttp.web.Response:
        return aiohttp.web.json_response(
            {
                "headless_callback_url": "headless_callback_url",
                "admin_url": "admin_url",
                "auth_url": "auth_url",
                "token_url": "token_url",
                "client_id": "client_id",
                "logout_url": "logout_url",
                "audience": "audience",
                "clusters": [
                    {
                        "name": "default",
                        "registry_url": "registry_url",
                        "storage_url": "storage_url",
                        "buckets_url": "buckets_url",
                        "users_url": "users_url",
                        "monitoring_url": "monitoring_url",
                        "secrets_url": "secrets_url",
                        "disks_url": "disks_url",
                        "resource_presets": [],
                    }
                ],
            }
        )

    async def _get_job(request: aiohttp.web.Request) -> aiohttp.web.Response:
        job_id = request.match_info["job_id"]
        return aiohttp.web.json_response(
            {
                "id": job_id,
                "owner": "user",
                "cluster_name": "default",
                "status": "running",
                "history": {"status": "running"},
                "container": {
                    "image": "neuromation/base",
                    "resources": {"cpu": 0.1, "memory_mb": 128},
                },
                "is_preemptible": False,
                "uri": f"job://default/user/{job_id}",
                "total_price_credits": "10",
                "price_credits_per_hour": "10",
                "pass_config": False,
                "scheduler_enabled": False,
            }
        )

    app = aiohttp.web.Application()
    app.router.add_get("/api/v1/config", _get_config)
    app.router.add_get("/api/v1/jobs/{job_id}", _get_job)
    return app
