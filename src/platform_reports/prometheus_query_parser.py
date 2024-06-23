from __future__ import annotations

import abc
import enum
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lark import Lark, LarkError, Token, Transformer, Tree, v_args
from lark.tree import Meta

from .prometheus_grammars import PROMQL


logger = logging.getLogger(__name__)


promql_parser = Lark(PROMQL, parser="lalr")


class PromQLException(ValueError):
    pass


class LabelMatcherOperator(enum.Enum):
    EQ = "="
    NE = "!="
    RE = "=~"
    NRE = "!~"

    @property
    def is_eq(self) -> bool:
        return self == self.EQ


@dataclass(frozen=True)
class LabelMatcher:
    name: str
    value: str
    operator: LabelMatcherOperator

    def __repr__(self) -> str:
        return repr(f"{self.name}{self.operator.value}{self.value}")

    @property
    def is_eq(self) -> bool:
        return self.operator.is_eq

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
    def equal(cls, name: str, value: str) -> LabelMatcher:
        return cls(name=name, value=value, operator=LabelMatcherOperator.EQ)

    @classmethod
    def not_equal(cls, name: str, value: str) -> LabelMatcher:
        return cls(name=name, value=value, operator=LabelMatcherOperator.NE)

    @classmethod
    def regex(cls, name: str, value: str) -> LabelMatcher:
        return cls(name=name, value=value, operator=LabelMatcherOperator.RE)

    @classmethod
    def not_regex(cls, name: str, value: str) -> LabelMatcher:
        return cls(name=name, value=value, operator=LabelMatcherOperator.NRE)


class Vector(abc.ABC):  # noqa: B024
    pass


@dataclass(frozen=True)
class VectorMatch(Vector):
    left: Vector
    right: Vector
    operator: str
    on: Sequence[str] = field(default_factory=list)
    ignoring: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class InstantVector(Vector):
    name: str
    label_matchers: Mapping[str, LabelMatcher] = field(default_factory=dict)

    def is_from_job(self, job: str) -> bool:
        return self.label_matchers["job"].matches(job)

    def get_eq_label_matcher(self, name: str) -> LabelMatcher | None:
        matcher = self.label_matchers.get(name)
        return matcher if matcher and matcher.is_eq else None


def parse_query(query: str) -> Vector | None:
    try:
        ast = promql_parser.parse(query)
    except LarkError as ex:
        logger.info("Failed to parse PromQL query: %s", ex, exc_info=True)
        msg = "Failed to parse PromQL query"
        raise PromQLException(msg) from ex
    transformer = VectorTransformer()
    return transformer.transform(ast)


class VectorTransformer(Transformer[Token, Vector | None]):
    def __default__(self, data: str, children: list[Any], meta: Meta) -> Vector | None:
        for child in children:
            if isinstance(child, Vector):
                return child
        return None

    @v_args(tree=True)
    def label_matcher_list(self, tree: Tree[Token]) -> Tree[Token]:
        return tree

    @v_args(tree=True)
    def label_matcher(self, tree: Tree[Token]) -> Tree[Token]:
        return tree

    @v_args(tree=True)
    def grouping(self, tree: Tree[Token]) -> Tree[Token]:
        return tree

    @v_args(tree=True)
    def on(self, tree: Tree[Token]) -> Tree[Token]:
        return tree

    @v_args(tree=True)
    def ignoring(self, tree: Tree[Token]) -> Tree[Token]:
        return tree

    @v_args(tree=True)
    def group_left(self, tree: Tree[Token]) -> Tree[Token]:
        return tree

    @v_args(tree=True)
    def group_right(self, tree: Tree[Token]) -> Tree[Token]:
        return tree

    @v_args(tree=True)
    def label_name_list(self, tree: Tree[Token]) -> Tree[Token]:
        return tree

    def instant_query_with_metric(self, children: list[Token | Tree[Token]]) -> Vector:
        label_matchers: list[Tree[Token]] = []
        if len(children) > 1:
            label_matchers = children[1].children  # type: ignore
        return InstantVector(
            name=children[0],  # type: ignore
            label_matchers=self._get_label_matchers(label_matchers),
        )

    def instant_query_without_metric(
        self, children: list[Token | Tree[Token]]
    ) -> Vector:
        label_matchers: list[Tree[Token]] = []
        if children:
            label_matchers = children[0].children  # type: ignore
        return InstantVector(
            name="", label_matchers=self._get_label_matchers(label_matchers)
        )

    def or_match(self, children: list[Token | Tree[Token]]) -> Vector | None:
        return self._get_vector_match(children)

    def and_unless_match(self, children: list[Token | Tree[Token]]) -> Vector | None:
        return self._get_vector_match(children)

    def comparison_match(self, children: list[Token | Tree[Token]]) -> Vector | None:
        return self._get_vector_match(children)

    def sum_match(self, children: list[Token | Tree[Token]]) -> Vector | None:
        return self._get_vector_match(children)

    def product_match(self, children: list[Token | Tree[Token]]) -> Vector | None:
        return self._get_vector_match(children)

    def power_match(self, children: list[Token | Tree[Token]]) -> Vector | None:
        return self._get_vector_match(children)

    @classmethod
    def _get_label_matchers(
        cls, label_matchers: list[Tree[Token]]
    ) -> dict[str, LabelMatcher]:
        result: dict[str, LabelMatcher] = {}
        for label_matcher in label_matchers:
            name = label_matcher.children[0]
            assert isinstance(name, str)
            result[name] = LabelMatcher(
                name=name,
                operator=LabelMatcherOperator(label_matcher.children[1]),
                value=label_matcher.children[2][1:-1],  # type: ignore
            )
        return result

    @classmethod
    def _get_vector_match(cls, children: list[Token | Tree[Token]]) -> Vector | None:
        vectors: list[Vector] = []
        for child in children:
            if isinstance(child, Vector):
                vectors.append(child)
        if not vectors:
            return None
        if len(vectors) > 2:
            msg = "Operation has invalid number of arguments"
            raise PromQLException(msg)
        if len(vectors) == 1:
            return vectors[0]
        grouping: Tree[Token] | None = None
        if len(children) > 3:
            grouping = children[2]  # type: ignore
        return VectorMatch(
            left=vectors[0],
            right=vectors[1],
            operator=children[1],  # type: ignore
            on=cls._get_on_labels(grouping),
            ignoring=cls._get_ignoring_labels(grouping),
        )

    @classmethod
    def _get_on_labels(cls, grouping: Tree[Token] | None) -> list[str]:
        if not grouping:
            return []
        if grouping.children[0].data == "on":  # type: ignore
            return grouping.children[0].children[1].children  # type: ignore
        return []

    @classmethod
    def _get_ignoring_labels(cls, grouping: Tree[Token] | None) -> list[str]:
        if not grouping:
            return []
        if grouping.children[0].data == "ignoring":  # type: ignore
            return grouping.children[0].children[1].children  # type: ignore
        return []
