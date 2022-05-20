from __future__ import annotations

from collections.abc import Sequence

import pytest

from platform_reports.prometheus import (
    InstantVector,
    LabelMatcher,
    PromQLException,
    VectorMatch,
    parse_query,
)


class TestDashboards:
    def test_all_dashboards_expressions(
        self, dashboards_expressions: dict[str, Sequence[str]]
    ) -> None:
        for key, exprs in dashboards_expressions.items():
            for expr in exprs:
                parse_query(expr)


class TestParseQueries:
    def test_raises_if_invalid_syntax(self) -> None:
        with pytest.raises(PromQLException):
            parse_query("1_invalid_metric_name")

    def test_scalars(self) -> None:
        result = parse_query("1 * 1")
        assert result is None

    def test_instant_vector(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

    def test_metric_without_name(self) -> None:
        result = parse_query("{__name__='container_cpu_usage_seconds_total'}")
        assert result == InstantVector(
            name="",
            label_matchers={
                "__name__": LabelMatcher.equal(
                    name="__name__", value="container_cpu_usage_seconds_total"
                )
            },
        )

    def test_instant_vector_with_keyword_label_matcher(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total{and='value'}")
        assert result == InstantVector(
            name="container_cpu_usage_seconds_total",
            label_matchers={"and": LabelMatcher.equal(name="and", value="value")},
        )

    def test_instant_vector_with_empty_label_matchers(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total{}")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

    def test_instant_vector_with_number(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total * 1")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

    def test_instant_vector_with_single_quoted_string(self) -> None:
        result = parse_query("count_values('\\t version \\t', build_version)")
        assert result == InstantVector(name="build_version")

    def test_instant_vector_with_double_quoted_string(self) -> None:
        result = parse_query('count_values("\\t version \\t", build_version)')
        assert result == InstantVector(name="build_version")

    def test_instant_vector_with_single_double_quoted_label_matchers(self) -> None:
        result = parse_query(
            """
            container_cpu_usage_seconds_total {
                job=~\"\\t kubelet \\t\", pod='\\t job \\t'
            }
            """
        )
        assert result == InstantVector(
            name="container_cpu_usage_seconds_total",
            label_matchers={
                "job": LabelMatcher.regex(name="job", value="\\t kubelet \\t"),
                "pod": LabelMatcher.equal(name="pod", value="\\t job \\t"),
            },
        )

    def test_instant_vector_with_empty_label_matcher_values(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total{pod!='',image!=\"\"}")
        assert result == InstantVector(
            name="container_cpu_usage_seconds_total",
            label_matchers={
                "pod": LabelMatcher.not_equal(name="pod", value=""),
                "image": LabelMatcher.not_equal(name="image", value=""),
            },
        )

    def test_range_vector_with_interval(self) -> None:
        result = parse_query("irate(container_cpu_usage_seconds_total[5m])")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

    def test_instant_vector_with_subquery(self) -> None:
        result = parse_query("irate(container_cpu_usage_seconds_total[5m:])")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

        result = parse_query("irate(container_cpu_usage_seconds_total[5m:1m])")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

        result = parse_query("irate(container_cpu_usage_seconds_total[5m])[30m:1m]")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

    def test_instant_vector_with_offset(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total offset 5m")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

        result = parse_query("container_cpu_usage_seconds_total[5m] offset 5m")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

        result = parse_query("container_cpu_usage_seconds_total[5m:1m] offset 5m")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

        result = parse_query("irate(container_cpu_usage_seconds_total[5m]) offset 30m")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

    def test_instant_vector_with_aggregation(self) -> None:
        result = parse_query(
            """
            sum by (pod) (
                irate(container_cpu_usage_seconds_total{job="kubelet"}[5m])
            )
            """
        )
        assert result == InstantVector(
            name="container_cpu_usage_seconds_total",
            label_matchers={"job": LabelMatcher.equal(name="job", value="kubelet")},
        )

        result = parse_query(
            "sum(irate(container_cpu_usage_seconds_total[5m])) by (pod)"
        )
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

        result = parse_query("sum(irate(container_cpu_usage_seconds_total[5m]))")
        assert result == InstantVector(name="container_cpu_usage_seconds_total")

    def test_one_to_many_match(self) -> None:
        result = parse_query(
            """
            sum by (pod) (
                irate(container_cpu_usage_seconds_total{job="kubelet"}[5m])
            )
            + on (pod) group_left (container)
            sum by (pod) (container_memory_usage_bytes{job="kubelet"})
            """
        )
        assert result == VectorMatch(
            left=InstantVector(
                name="container_cpu_usage_seconds_total",
                label_matchers={"job": LabelMatcher.equal(name="job", value="kubelet")},
            ),
            right=InstantVector(
                name="container_memory_usage_bytes",
                label_matchers={"job": LabelMatcher.equal(name="job", value="kubelet")},
            ),
            operator="+",
            on=["pod"],
        )

        result = parse_query(
            """
            sum by (pod) (irate(container_cpu_usage_seconds_total[5m]))
            + on (pod) group_right (container)
            sum by (pod) (container_memory_usage_bytes)
            """
        )
        assert result == VectorMatch(
            left=InstantVector(name="container_cpu_usage_seconds_total"),
            right=InstantVector(name="container_memory_usage_bytes"),
            operator="+",
            on=["pod"],
        )

    def test_match_with_on(self) -> None:
        result = parse_query(
            """
            sum by (pod) (irate(container_cpu_usage_seconds_total[5m]))
            + on (pod)
            sum by (pod) (container_memory_usage_bytes)
            """
        )
        assert result == VectorMatch(
            left=InstantVector(name="container_cpu_usage_seconds_total"),
            right=InstantVector(name="container_memory_usage_bytes"),
            operator="+",
            on=["pod"],
        )

    def test_match_with_ignoring(self) -> None:
        result = parse_query(
            """
            sum by (pod) (irate(container_cpu_usage_seconds_total[5m]))
            + ignoring (pod)
            sum by (pod) (container_memory_usage_bytes)
            """
        )
        assert result == VectorMatch(
            left=InstantVector(name="container_cpu_usage_seconds_total"),
            right=InstantVector(name="container_memory_usage_bytes"),
            operator="+",
            ignoring=["pod"],
        )

    def test_match_is_left_associative(self) -> None:
        result = parse_query(
            """
            sum by (pod) (irate(container_cpu_usage_seconds_total[5m]))
            -
            sum by (pod) (container_memory_usage_bytes)
            -
            sum by (pod) (container_memory_usage_bytes)
            """
        )
        assert result == VectorMatch(
            left=VectorMatch(
                left=InstantVector(name="container_cpu_usage_seconds_total"),
                right=InstantVector(name="container_memory_usage_bytes"),
                operator="-",
            ),
            right=InstantVector(name="container_memory_usage_bytes"),
            operator="-",
        )

    def test_match_with_parens(self) -> None:
        result = parse_query(
            """
            sum by (pod) (irate(container_cpu_usage_seconds_total[5m]))
            - on (pod)
            (
                sum by (pod) (container_memory_usage_bytes)
                - ignoring (pod)
                sum by (pod) (container_memory_usage_bytes)
            )
            """
        )
        assert result == VectorMatch(
            left=InstantVector(name="container_cpu_usage_seconds_total"),
            right=VectorMatch(
                left=InstantVector(name="container_memory_usage_bytes"),
                right=InstantVector(name="container_memory_usage_bytes"),
                operator="-",
                ignoring=["pod"],
            ),
            operator="-",
            on=["pod"],
        )
