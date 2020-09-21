import re
from pathlib import Path
from typing import Dict, List, Sequence

import pytest


@pytest.fixture(scope="session")
def dashboards_expressions() -> Dict[str, Sequence[str]]:
    result: Dict[str, Sequence[str]] = {}
    dashboards_path = Path("deploy/platform-reports/dashboards")
    expr_regex = r'"expr": "((?:[^"\\]|\\.)+)"'
    for path in dashboards_path.glob("**/*.json"):
        exprs: List[str] = []
        for match in re.finditer(expr_regex, path.read_text()):
            exprs.append(
                match.group(1)
                .replace('\\"', '"')
                .replace("$__range", "15m")
                .replace("$__interval", "15s")
            )
        if not exprs:
            continue
        key = str(path.relative_to(dashboards_path)).rstrip(".json")
        result[key] = exprs
    return result


@pytest.fixture(scope="session")
def admin_dashboards_expressions(
    dashboards_expressions: Dict[str, Sequence[str]]
) -> Dict[str, Sequence[str]]:
    result: Dict[str, Sequence[str]] = {}
    for key, exprs in dashboards_expressions.items():
        if key.startswith("admin/"):
            result[key] = exprs
    return result


@pytest.fixture(scope="session")
def user_dashboards_expressions(
    dashboards_expressions: Dict[str, Sequence[str]]
) -> Dict[str, Sequence[str]]:
    result: Dict[str, Sequence[str]] = {}
    for key, exprs in dashboards_expressions.items():
        if key.startswith("user/"):
            result[key] = [expr.replace("$user_name", "user") for expr in exprs]
    return result
