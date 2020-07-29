from platform_reports.prometheus import (
    Filter,
    FilterOperator,
    Metric,
    parse_query_metrics,
)


class TestParseQueryMetrics:
    def test_metric(self) -> None:
        result = parse_query_metrics("container_cpu_usage_seconds_total")
        assert result == [Metric(name="container_cpu_usage_seconds_total", filters={})]

    def test_metric_without_name(self) -> None:
        result = parse_query_metrics("{__name__='container_cpu_usage_seconds_total'}")
        assert result == [
            Metric(
                name="",
                filters={
                    "__name__": Filter(
                        label="__name__",
                        value="container_cpu_usage_seconds_total",
                        operator=FilterOperator.EQ,
                    )
                },
            )
        ]

    def test_metric_with_empty_filters(self) -> None:
        result = parse_query_metrics("container_cpu_usage_seconds_total{}")
        assert result == [Metric(name="container_cpu_usage_seconds_total", filters={})]

    def test_metric_with_number(self) -> None:
        result = parse_query_metrics("container_cpu_usage_seconds_total * 1")
        assert result == [Metric(name="container_cpu_usage_seconds_total", filters={})]

    def test_metric_with_single_quoted_string(self) -> None:
        result = parse_query_metrics("count_values('\\t version \\t', build_version)")
        assert result == [Metric(name="build_version", filters={})]

    def test_metric_with_double_quoted_string(self) -> None:
        result = parse_query_metrics('count_values("\\t version \\t", build_version)')
        assert result == [Metric(name="build_version", filters={})]

    def test_metric_with_single_double_quoted_filters(self) -> None:
        result = parse_query_metrics(
            """
            container_cpu_usage_seconds_total {
                job=~\"\\t kubelet \\t\", pod='\\t job \\t'
            }
            """
        )
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                filters={
                    "job": Filter(
                        label="job", value="\\t kubelet \\t", operator=FilterOperator.RE
                    ),
                    "pod": Filter(
                        label="pod", value="\\t job \\t", operator=FilterOperator.EQ
                    ),
                },
            )
        ]

    def test_metric_with_empty_filter_values(self) -> None:
        result = parse_query_metrics(
            "container_cpu_usage_seconds_total{pod!='',image!=\"\"}"
        )
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                filters={
                    "pod": Filter(label="pod", value="", operator=FilterOperator.NE),
                    "image": Filter(
                        label="image", value="", operator=FilterOperator.NE
                    ),
                },
            )
        ]

    def test_metric_with_operator(self) -> None:
        result = parse_query_metrics("sum(container_cpu_usage_seconds_total)")
        assert result == [Metric(name="container_cpu_usage_seconds_total", filters={})]

    def test_metric_with_interval(self) -> None:
        result = parse_query_metrics("irate(container_cpu_usage_seconds_total[5m])")
        assert result == [Metric(name="container_cpu_usage_seconds_total", filters={})]

    def test_metric_with_aggregation(self) -> None:
        result = parse_query_metrics(
            """
            sum by (pod) (
                irate(container_cpu_usage_seconds_total{job="kubelet"}[5m])
            )
            """
        )
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                filters={
                    "job": Filter(
                        label="job", value="kubelet", operator=FilterOperator.EQ
                    )
                },
            )
        ]

    def test_metric_with_join(self) -> None:
        result = parse_query_metrics(
            """
            sum by (pod) (
                irate(container_cpu_usage_seconds_total{job="kubelet"}[5m])
            )
            on(pod) group_left(container)
            sum by (pod,container) (container_memory_usage_bytes{job="kubelet"})
            """
        )
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                filters={
                    "job": Filter(
                        label="job", value="kubelet", operator=FilterOperator.EQ
                    )
                },
            ),
            Metric(
                name="container_memory_usage_bytes",
                filters={
                    "job": Filter(
                        label="job", value="kubelet", operator=FilterOperator.EQ
                    )
                },
            ),
        ]
