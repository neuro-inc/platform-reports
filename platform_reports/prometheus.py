import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Union

from lark import Lark, Transformer, v_args
from lark.exceptions import LarkError
from lark.tree import Meta, Tree

from .prometheus_grammars import PROMQL


logger = logging.getLogger(__name__)


promql_parser = Lark(PROMQL, parser="lalr")


class PromQLException(Exception):
    pass


class LabelMatcherOperator(enum.Enum):
    EQ = "="
    NE = "!="
    RE = "=~"
    NRE = "!~"


@dataclass(frozen=True)
class LabelMatcher:
    name: str
    value: str
    operator: LabelMatcherOperator

    def __repr__(self) -> str:
        return repr(f"{self.name}{self.operator.value}{self.value}")

    def matches(self, label_value: str) -> bool:
        if self.operator == LabelMatcherOperator.EQ:
            return self.value == label_value
        if self.operator == LabelMatcherOperator.NE:
            return self.value != label_value
        if self.operator == LabelMatcherOperator.RE:
            return bool(re.match(self.value, label_value))
        if self.operator == LabelMatcherOperator.NRE:
            return not re.match(self.value, label_value)
        return False

    @classmethod
    def equal(cls, name: str, value: str) -> "LabelMatcher":
        return cls(name=name, value=value, operator=LabelMatcherOperator.EQ)

    @classmethod
    def not_equal(cls, name: str, value: str) -> "LabelMatcher":
        return cls(name=name, value=value, operator=LabelMatcherOperator.NE)

    @classmethod
    def regex(cls, name: str, value: str) -> "LabelMatcher":
        return cls(name=name, value=value, operator=LabelMatcherOperator.RE)

    @classmethod
    def not_regex(cls, name: str, value: str) -> "LabelMatcher":
        return cls(name=name, value=value, operator=LabelMatcherOperator.NRE)


@dataclass(frozen=True)
class Metric:
    name: str
    label_matchers: Mapping[str, LabelMatcher] = field(default_factory=dict)


def parse_query(query: str) -> Sequence[Metric]:
    try:
        ast = promql_parser.parse(query)
    except LarkError as ex:
        logger.info("Error while parsing PromQL query: %s", ex)
        raise PromQLException(f"Error while parsing PromQL query: {ex}")
    transformer = VectorTransformer()
    return transformer.transform(ast)


class VectorTransformer(Transformer[List[Metric]]):
    def __default__(self, data: str, children: List[Any], meta: Meta) -> List[Metric]:
        result: List[Metric] = []
        for child in children:
            if isinstance(child, list):
                result.extend(child)
        return result

    @v_args(tree=True)
    def label_matcher_list(self, tree: Tree) -> Tree:
        return tree

    @v_args(tree=True)
    def label_matcher(self, tree: Tree) -> Tree:
        return tree

    def instant_selector_with_metric(
        self, children: List[Union[str, Tree]]
    ) -> List[Metric]:
        label_matchers: List[Tree] = []
        if len(children) > 1:
            label_matchers = children[1].children  # type: ignore
        return [
            Metric(
                name=children[0].value,  # type: ignore
                label_matchers=self._get_label_matchers(label_matchers),
            )
        ]

    def instant_selector_without_metric(
        self, children: List[Union[str, Tree]]
    ) -> List[Metric]:
        label_matchers: List[Tree] = []
        if children:
            label_matchers = children[0].children  # type: ignore
        return [
            Metric(name="", label_matchers=self._get_label_matchers(label_matchers))
        ]

    def _get_label_matchers(
        self, label_matchers: List[Tree]
    ) -> Dict[str, LabelMatcher]:
        result: Dict[str, LabelMatcher] = {}
        for label_matcher in label_matchers:
            name = label_matcher.children[0].value  # type: ignore
            result[name] = LabelMatcher(
                name=name,
                operator=LabelMatcherOperator(
                    label_matcher.children[1].value  # type: ignore
                ),
                value=label_matcher.children[2].value[1:-1],  # type: ignore
            )
        return result
