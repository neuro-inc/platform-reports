import io
import json
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml


CHART_PATH = Path("charts/platform-reports")
RULES_PATH = CHART_PATH / "files/prometheus-rules/log-streams.yaml"
RECORD = "namespace_app:loki_log_lines:count1m"
COUNTER = "loki_process_custom_log_entries_total"
LOG_PERIOD = 300
LOG_LINES = 2
DURATION = 80 * 60
EVAL_TIME = "74m30s"
WINDOW_EVALUATIONS = 60
BATCH_SECOND = 30 * 60


def _seconds(duration: str) -> float:
    units = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    parts = re.findall(r"(\d+)(ms|s|m|h)", duration)
    assert "".join(number + unit for number, unit in parts) == duration
    return sum(int(number) * units[unit] for number, unit in parts)


def _values() -> dict[str, Any]:
    return yaml.safe_load((CHART_PATH / "values.yaml").read_text())


def _rule_group() -> dict[str, Any]:
    return yaml.safe_load(RULES_PATH.read_text())["groups"][0]


def _prometheus_image() -> str:
    image = _values()["kube-prometheus-stack"]["prometheus"]["prometheusSpec"]["image"]
    return f"{image['registry']}/{image['repository']}:{image['tag']}"


SCRAPE_INTERVAL = int(
    _seconds(
        _values()["kube-prometheus-stack"]["prometheus"]["prometheusSpec"][
            "scrapeInterval"
        ]
    )
)
EVALUATION_INTERVAL = int(_seconds(_rule_group()["interval"]))


def _labels(app: str) -> str:
    return f'{{namespace="platform",app="{app}"}}'


def _series(samples: dict[int, int]) -> str:
    values: list[str] = []
    gap = 0
    for second in range(DURATION + 1):
        if second in samples:
            if gap:
                values.append(f"_x{gap}")
                gap = 0
            values.append(str(samples[second]))
        else:
            gap += 1
    if gap:
        values.append(f"_x{gap}")
    return " ".join(values)


def _steady(
    phase: int,
    *,
    late: int | None = None,
    missed: int | None = None,
    skipped_from: int | None = None,
) -> dict[int, int]:
    samples: dict[int, int] = {}
    for second in range(phase % SCRAPE_INTERVAL, DURATION + 1, SCRAPE_INTERVAL):
        batches = 0 if second < phase else (second - phase) // LOG_PERIOD + 1
        if skipped_from is not None and second >= skipped_from:
            batches -= 1
        if second == missed:
            continue
        samples[second + 1 if second == late else second] = 100 + LOG_LINES * batches
    return samples


def _new_stream(phase: int, first: int, later: int) -> dict[int, int]:
    start = 40 * 60 + phase
    return {
        second: first if second == start else later
        for second in range(start, DURATION + 1, SCRAPE_INTERVAL)
    }


def _step(before: int, after: int) -> dict[int, int]:
    return {
        second: before if second < BATCH_SECOND else after
        for second in range(0, DURATION + 1, SCRAPE_INTERVAL)
    }


def _reset(phase: int, after: int) -> dict[int, int]:
    return {
        second: 1000 if second < phase + BATCH_SECOND else after
        for second in range(phase % SCRAPE_INTERVAL, DURATION + 1, SCRAPE_INTERVAL)
    }


def _promtool_test_rules(
    series: dict[str, dict[int, int]], tests: list[dict[str, Any]]
) -> subprocess.CompletedProcess[bytes]:
    test = {
        "rule_files": ["log-streams.yaml"],
        "evaluation_interval": f"{EVALUATION_INTERVAL}s",
        "tests": [
            {
                "interval": "1s",
                "input_series": [
                    {"series": COUNTER + _labels(app), "values": _series(samples)}
                    for app, samples in series.items()
                ],
                "promql_expr_test": tests,
            }
        ],
    }
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, content in {
            "log-streams.yaml": RULES_PATH.read_text(),
            "test.json": json.dumps(test),
        }.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network=none",
            "--entrypoint",
            "sh",
            _prometheus_image(),
            "-c",
            "mkdir /tmp/rules && tar -x -f - -C /tmp/rules"
            " && promtool test rules /tmp/rules/test.json",
        ],
        input=archive.getvalue(),
        capture_output=True,
        check=False,
    )


def _assert_hourly_sums(
    series: dict[str, dict[int, int]],
    expected: dict[str, float],
    evaluations: int | None = WINDOW_EVALUATIONS,
) -> None:
    tests: list[dict[str, Any]] = [
        {
            "expr": f"sum_over_time({RECORD}[1h])",
            "eval_time": EVAL_TIME,
            "exp_samples": [
                {"labels": _labels(app), "value": value}
                for app, value in expected.items()
            ],
        }
    ]
    if evaluations is not None:
        tests.append(
            {
                "expr": f"count_over_time({RECORD}[1h])",
                "eval_time": EVAL_TIME,
                "exp_samples": [
                    {"labels": _labels(app), "value": evaluations} for app in series
                ],
            }
        )

    result = _promtool_test_rules(series, tests)

    assert result.returncode == 0, (result.stdout + result.stderr).decode()


def test_log_streams_rule_range_is_evaluation_plus_scrape_interval() -> None:
    expr = _rule_group()["rules"][0]["expr"]
    window = (EVALUATION_INTERVAL + SCRAPE_INTERVAL) * 1000 - 1

    assert re.findall(r"\[([^\]]+)\]", expr) == [f"{window}ms"]


def test_log_streams_rule_counts_every_line_at_any_scrape_phase() -> None:
    batches = WINDOW_EVALUATIONS * EVALUATION_INTERVAL // LOG_PERIOD
    series: dict[str, dict[int, int]] = {}
    expected: dict[str, float] = {}
    for phase in range(EVALUATION_INTERVAL):
        series[f"phase-{phase}"] = _steady(phase)
        expected[f"phase-{phase}"] = batches * LOG_LINES
        series[f"skipped-batch-phase-{phase}"] = _steady(
            phase, skipped_from=phase + BATCH_SECOND
        )
        expected[f"skipped-batch-phase-{phase}"] = (batches - 1) * LOG_LINES

    _assert_hourly_sums(series, expected)


def test_log_streams_rule_counts_lines_after_counter_reset() -> None:
    series: dict[str, dict[int, int]] = {}
    expected: dict[str, float] = {}
    for phase in range(EVALUATION_INTERVAL):
        for after in (3, 0):
            series[f"reset-to-{after}-phase-{phase}"] = _reset(phase, after)
            expected[f"reset-to-{after}-phase-{phase}"] = after

    _assert_hourly_sums(series, expected)


@pytest.mark.parametrize(
    ("app", "samples", "value", "evaluations"),
    [
        (
            "new-stream-large-first-value",
            _new_stream(7, 4691, 4693),
            2.151139792975018,
            None,
        ),
        (
            "new-stream-from-zero-phase-0",
            _new_stream(0, 0, 2),
            1.6000213336177815,
            None,
        ),
        (
            "new-stream-from-zero-phase-31",
            _new_stream(31, 0, 2),
            3.093374578327711,
            None,
        ),
        (
            "late-scrape-phase-1",
            _steady(1, late=1 + BATCH_SECOND),
            24,
            WINDOW_EVALUATIONS,
        ),
        (
            "late-scrape-phase-46",
            _steady(46, late=46 + BATCH_SECOND),
            23.9672131147541,
            WINDOW_EVALUATIONS,
        ),
        (
            "late-scrape-at-evaluation",
            _steady(0, late=BATCH_SECOND),
            22,
            WINDOW_EVALUATIONS,
        ),
        (
            "late-scrape-before-batch-at-evaluation",
            _steady(0, late=BATCH_SECOND - SCRAPE_INTERVAL),
            25.62162162162162,
            WINDOW_EVALUATIONS,
        ),
        (
            "large-increment-on-small-counter",
            _step(100, 1100),
            880.01173348978,
            WINDOW_EVALUATIONS,
        ),
        (
            "large-increment-on-large-counter",
            _step(100000, 101000),
            1000.0000000000001,
            WINDOW_EVALUATIONS,
        ),
        (
            "missed-scrape-phase-1",
            _steady(1, missed=1 + BATCH_SECOND),
            24,
            WINDOW_EVALUATIONS,
        ),
        (
            "missed-scrape-phase-46",
            _steady(46, missed=46 + BATCH_SECOND),
            22,
            WINDOW_EVALUATIONS,
        ),
    ],
)
def test_log_streams_rule_known_errors(
    app: str, samples: dict[int, int], value: float, evaluations: int | None
) -> None:
    _assert_hourly_sums({app: samples}, {app: value}, evaluations)
