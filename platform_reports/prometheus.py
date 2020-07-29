import enum
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence


_OPERATORS = [
    "sum",
    "min",
    "max",
    "avg",
    "group",
    "stddev",
    "stdvar",
    "count",
    "count_values",
    "bottomk",
    "topk",
    "quantile",
    "and",
    "or",
    "unless",
]

_FUNCTIONS = [
    "abs",
    "absent",
    "absent_over_time",
    "ceil",
    "changes",
    "clamp_max",
    "clamp_min",
    "day_of_month",
    "day_of_week",
    "days_in_month",
    "delta",
    "deriv",
    "exp",
    "floor",
    "histogram_quantile",
    "holt_winters",
    "hour",
    "idelta",
    "increase",
    "irate",
    "label_join",
    "label_replace",
    "ln",
    "log2",
    "log10",
    "minute",
    "month",
    "predict_linear",
    "rate",
    "resets",
    "round",
    "scalar",
    "sort",
    "sort_desc",
    "sqrt",
    "time",
    "timestamp",
    "vector",
    "year",
    "avg_over_time",
    "min_over_time",
    "max_over_time",
    "sum_over_time",
    "count_over_time",
    "quantile_over_time",
    "stddev_over_time",
    "stdvar_over_time",
]

_METRICS_RE = re.compile(
    "|".join(
        [
            r"-?[0-9]+(?:\.[0-9]+)?",  # number
            r"'(?:[^'\\]|\\.)*'",  # single quoted string
            r'"(?:[^"\\]|\\.)*"',  # double quoted string
            r"(?:by|without)[ \t]*\([^)]+\)",  # aggregations
            r"(?:on|ignoring|group_left|group_right)[ \t]*\([^)]+\)",  # joins
            r"\[[^]]+\]",  # interval
            *sorted(_OPERATORS + _FUNCTIONS, key=len, reverse=True),
            r"(?P<name>[0-9a-zA-Z_]+)?[ \t]*(?P<filters>\{[^}]*\})?",  # metric
        ]
    )
)

_FILTER_RE = (
    r"(?P<label>[0-9a-zA-Z_]+)"
    r"(?P<op>\=|\=\~|\!\=|\!\~)"
    r"(?:'(?P<sq_value>(?:[^'\\]|\\.)*)'|\"(?P<dq_value>(?:[^\"\\]|\\.)*)\")"
)


class FilterOperator(enum.Enum):
    EQ = "="
    NE = "!="
    RE = "=~"
    NRE = "!~"


@dataclass(frozen=True)
class Filter:
    label: str
    value: str
    operator: FilterOperator

    def __repr__(self) -> str:
        return repr(f"{self.label}{self.operator.value}{self.value}")

    def matches(self, label_value: str) -> bool:
        if self.operator == FilterOperator.EQ:
            return self.value == label_value
        if self.operator == FilterOperator.NE:
            return self.value != label_value
        if self.operator == FilterOperator.RE:
            return bool(re.match(self.value, label_value))
        if self.operator == FilterOperator.NRE:
            return not re.match(self.value, label_value)
        return False


@dataclass(frozen=True)
class Metric:
    name: str
    filters: Mapping[str, Filter]


def parse_query_metrics(query: str) -> Sequence[Metric]:
    result: List[Metric] = []

    for match in re.finditer(_METRICS_RE, query):
        groups = match.groupdict()
        name = groups["name"] or ""
        filters = _parse_filters(groups["filters"])
        if name or filters:
            result.append(Metric(name=name, filters=filters))

    return result


def _parse_filters(filters_str: Optional[str]) -> Dict[str, Filter]:
    result: Dict[str, Filter] = {}

    if not filters_str:
        return result

    for match in re.finditer(_FILTER_RE, filters_str):
        groups = match.groupdict()

        label = groups["label"]
        assert label

        operator = groups["op"]
        assert operator

        sq_value = groups["sq_value"] or ""
        if sq_value:
            value = sq_value
        else:
            value = groups["dq_value"] or ""

        result[label] = Filter(
            label=label, operator=FilterOperator(operator), value=value
        )

    return result
