from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from itertools import chain
from pathlib import Path

import pytest


pytest_plugins = [
    "tests.integration.conftest_platform_auth",
    "tests.integration.conftest_platform_config",
    "tests.integration.conftest_platform_api",
    "tests.integration.conftest_kube",
]


@pytest.fixture(scope="session")
def dashboards_expressions() -> dict[str, Sequence[str]]:
    result: dict[str, Sequence[str]] = {}
    dashboards_path = Path("charts/platform-reports/files/grafana-dashboards")
    expr_regex = re.compile(r'"expr": "((?:[^"\\]|\\.)+)"')
    var_regex = re.compile(r'"query": "label_values\(((?:[^"\\]|\\.)+),[a-z_]+\)"')
    for path in dashboards_path.glob("**/*.json"):
        exprs: list[str] = []
        dashboard_json = path.read_text()
        for match in chain(
            expr_regex.finditer(dashboard_json), var_regex.finditer(dashboard_json)
        ):
            exprs.append(
                json.loads(f'"{match.group(1)}"')
                .replace("$__interval_ms", "15000")
                .replace("$__range", "15m")
                .replace("$__interval", "15s")
                .replace("$__rate_interval", "1m")
                .replace("$__from", "1604070620")
                .replace("$project_name", "project")
                .replace("$org_name", "org")
                .replace("$user_name", "user")
                .replace("$job_id", f"job-{uuid.uuid4()}")
            )
        if not exprs:
            continue
        key = str(path.relative_to(dashboards_path)).split(".")[0]
        result[key] = exprs
    return result


@pytest.fixture(scope="session")
def cluster_dashboards_expressions(
    dashboards_expressions: dict[str, Sequence[str]],
) -> dict[str, Sequence[str]]:
    result: dict[str, Sequence[str]] = {}
    for key, exprs in dashboards_expressions.items():
        if key.startswith("cluster/"):
            result[key] = exprs
    return result


@pytest.fixture(scope="session")
def project_dashboards_expressions(
    dashboards_expressions: dict[str, Sequence[str]],
) -> dict[str, Sequence[str]]:
    result: dict[str, Sequence[str]] = {}
    for key, exprs in dashboards_expressions.items():
        if key.startswith("project/"):
            result[key] = exprs
    return result


@pytest.fixture(scope="session")
def org_dashboards_expressions(
    dashboards_expressions: dict[str, Sequence[str]],
) -> dict[str, Sequence[str]]:
    result: dict[str, Sequence[str]] = {}
    for key, exprs in dashboards_expressions.items():
        if key.startswith("org/"):
            result[key] = exprs
    return result
