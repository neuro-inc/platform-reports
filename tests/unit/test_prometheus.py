from platform_reports.prometheus import LabelMatcher, Metric, parse_query


class TestParseQueryMetrics:
    def test_without_metric(self) -> None:
        result = parse_query("1 * 1")
        assert result == []

    def test_metric(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total")
        assert result == [Metric(name="container_cpu_usage_seconds_total")]

    def test_metric_without_name(self) -> None:
        result = parse_query("{__name__='container_cpu_usage_seconds_total'}")
        assert result == [
            Metric(
                name="",
                label_matchers={
                    "__name__": LabelMatcher.equal(
                        name="__name__", value="container_cpu_usage_seconds_total"
                    )
                },
            )
        ]

    def test_metric_with_keyword_label_matcher(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total{and='value'}")
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                label_matchers={"and": LabelMatcher.equal(name="and", value="value")},
            )
        ]

    def test_metric_with_empty_label_matchers(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total{}")
        assert result == [Metric(name="container_cpu_usage_seconds_total")]

    def test_metric_with_number(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total * 1")
        assert result == [Metric(name="container_cpu_usage_seconds_total")]

    def test_metric_with_single_quoted_string(self) -> None:
        result = parse_query("count_values('\\t version \\t', build_version)")
        assert result == [Metric(name="build_version")]

    def test_metric_with_double_quoted_string(self) -> None:
        result = parse_query('count_values("\\t version \\t", build_version)')
        assert result == [Metric(name="build_version")]

    def test_metric_with_single_double_quoted_label_matchers(self) -> None:
        result = parse_query(
            """
            container_cpu_usage_seconds_total {
                job=~\"\\t kubelet \\t\", pod='\\t job \\t'
            }
            """
        )
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                label_matchers={
                    "job": LabelMatcher.regex(name="job", value="\\t kubelet \\t"),
                    "pod": LabelMatcher.equal(name="pod", value="\\t job \\t"),
                },
            )
        ]

    def test_metric_with_empty_label_matcher_values(self) -> None:
        result = parse_query("container_cpu_usage_seconds_total{pod!='',image!=\"\"}")
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                label_matchers={
                    "pod": LabelMatcher.not_equal(name="pod", value=""),
                    "image": LabelMatcher.not_equal(name="image", value=""),
                },
            )
        ]

    def test_metric_with_interval(self) -> None:
        result = parse_query("irate(container_cpu_usage_seconds_total[5m])")
        assert result == [Metric(name="container_cpu_usage_seconds_total")]

    def test_metric_with_aggregation(self) -> None:
        result = parse_query(
            """
            sum by (pod) (
                irate(container_cpu_usage_seconds_total{job="kubelet"}[5m])
            )
            """
        )
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                label_matchers={"job": LabelMatcher.equal(name="job", value="kubelet")},
            )
        ]

    def test_metrics_join(self) -> None:
        result = parse_query(
            """
            sum by (pod) (
                irate(container_cpu_usage_seconds_total{job="kubelet"}[5m])
            )
            + on (pod) group_left (container)
            sum by (pod,container) (container_memory_usage_bytes{job="kubelet"})
            """
        )
        assert result == [
            Metric(
                name="container_cpu_usage_seconds_total",
                label_matchers={"job": LabelMatcher.equal(name="job", value="kubelet")},
            ),
            Metric(
                name="container_memory_usage_bytes",
                label_matchers={"job": LabelMatcher.equal(name="job", value="kubelet")},
            ),
        ]
