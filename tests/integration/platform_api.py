import aiohttp.web
import pytest


@pytest.fixture
def platform_api_app() -> aiohttp.web.Application:
    async def _get_config(request: aiohttp.web.Request) -> aiohttp.web.Response:
        return aiohttp.web.json_response(
            {
                "headless_callback_url": "headless_callback_url",
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
                        "users_url": "users_url",
                        "monitoring_url": "monitoring_url",
                        "secrets_url": "secrets_url",
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
                "container": {"image": "neuromation/base", "env": {}, "volumes": []},
                "is_preemptible": False,
                "uri": f"job://default/user/{job_id}",
            }
        )

    app = aiohttp.web.Application()
    app.router.add_get("/api/v1/config", _get_config)
    app.router.add_get("/api/v1/jobs/{job_id}", _get_job)
    return app
