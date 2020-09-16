import re
from pathlib import Path
from typing import Dict, List, Sequence

import pytest


@pytest.fixture(scope="session")
def dashboard_expressions() -> Dict[str, Sequence[str]]:
    result: Dict[str, Sequence[str]] = {}
    dashboards_path = Path("deploy/platform-reports/dashboards")
    expr_regex = r'"expr": "((?:[^"\\]|\\.)+)"'
    for path in dashboards_path.glob("**/*.json"):
        exprs: List[str] = []
        for match in re.finditer(expr_regex, path.read_text()):
            exprs.append(match.group(1).replace('\\"', '"'))
        if not exprs:
            continue
        key = str(path.relative_to(dashboards_path)).rstrip(".json")
        result[key] = exprs
    return result
