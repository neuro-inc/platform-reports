from __future__ import annotations

import abc
import enum
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

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


class Vector(abc.ABC):
    @abc.abstractproperty
    def labels(self) -> Sequence[str]:
        pass


@dataclass(frozen=True)
class Join(Vector):
    left: Vector
    right: Vector
    operator: str
    on: Sequence[str] = field(default_factory=list)
    ignoring: Sequence[str] = field(default_factory=list)

    @property
    def labels(self) -> Sequence[str]:
        # If 'on' is used than left and right vectors must contain 'on' labels.
        if self.on:
            return self.on
        # If 'on' is not used than the labels list cannot be determined for now
        # because there are functions that change labels. If there is a request for
        # more accurate labels list query functions should be analyzed
        # by VectorTransformer.
        return []


@dataclass(frozen=True)
class Metric(Vector):
    name: str
    label_matchers: Mapping[str, LabelMatcher] = field(default_factory=dict)

    @property
    def labels(self) -> Sequence[str]:
        name_label = self.label_matchers.get("__name__")
        labels = set(self.label_matchers.keys())
        job_label = self.label_matchers.get("job")
        if job_label and job_label.matches("kubelet"):
            labels.add("pod")
        if (
            job_label
            and job_label.matches("kube-state-metrics")
            and (
                self.name.startswith("kube_pod_")
                or name_label
                and name_label.matches("kube_pod_")
            )
        ):
            labels.add("pod")
        return list(labels)


def parse_query(query: str) -> Vector | None:
    try:
        ast = promql_parser.parse(query)
    except LarkError as ex:
        logger.warning("Error while parsing PromQL query: %s", ex)
        raise PromQLException(f"Error while parsing PromQL query: {ex}")
    transformer = VectorTransformer()
    return transformer.transform(ast)


class VectorTransformer(Transformer[Optional[Vector]]):
    def __default__(self, data: str, children: list[Any], meta: Meta) -> Vector | None:
        for child in children:
            if isinstance(child, Vector):
                return child
        return None

    @v_args(tree=True)
    def label_matcher_list(self, tree: Tree) -> Tree:
        return tree

    @v_args(tree=True)
    def label_matcher(self, tree: Tree) -> Tree:
        return tree

    @v_args(tree=True)
    def grouping(self, tree: Tree) -> Tree:
        return tree

    @v_args(tree=True)
    def on(self, tree: Tree) -> Tree:
        return tree

    @v_args(tree=True)
    def ignoring(self, tree: Tree) -> Tree:
        return tree

    @v_args(tree=True)
    def group_left(self, tree: Tree) -> Tree:
        return tree

    @v_args(tree=True)
    def group_right(self, tree: Tree) -> Tree:
        return tree

    @v_args(tree=True)
    def label_name_list(self, tree: Tree) -> Tree:
        return tree

    def instant_selector_with_metric(self, children: list[str | Tree]) -> Vector:
        label_matchers: list[Tree] = []
        if len(children) > 1:
            label_matchers = children[1].children  # type: ignore
        return Metric(
            name=children[0].value,  # type: ignore
            label_matchers=self._get_label_matchers(label_matchers),
        )

    def instant_selector_without_metric(self, children: list[str | Tree]) -> Vector:
        label_matchers: list[Tree] = []
        if children:
            label_matchers = children[0].children  # type: ignore
        return Metric(name="", label_matchers=self._get_label_matchers(label_matchers))

    def or_join(self, children: list[str | Tree]) -> Vector | None:
        return self._get_join(children)

    def and_unless_join(self, children: list[str | Tree]) -> Vector | None:
        return self._get_join(children)

    def comparison_join(self, children: list[str | Tree]) -> Vector | None:
        return self._get_join(children)

    def sum_join(self, children: list[str | Tree]) -> Vector | None:
        return self._get_join(children)

    def product_join(self, children: list[str | Tree]) -> Vector | None:
        return self._get_join(children)

    def power_join(self, children: list[str | Tree]) -> Vector | None:
        return self._get_join(children)

    def _get_label_matchers(
        self, label_matchers: list[Tree]
    ) -> dict[str, LabelMatcher]:
        result: dict[str, LabelMatcher] = {}
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

    def _get_join(self, children: list[str | Tree]) -> Vector | None:
        vectors: list[Vector] = []
        for child in children:
            if isinstance(child, Vector):
                vectors.append(child)
        if not vectors:
            return None
        if len(vectors) > 2:
            raise PromQLException("Operation has invalid number of arguments")
        if len(vectors) == 1:
            return vectors[0]
        grouping: Tree | None = None
        if len(children) > 3:
            grouping = children[2]  # type: ignore
        assert not grouping or isinstance(grouping, Tree)
        return Join(
            left=vectors[0],
            right=vectors[1],
            operator=children[1].value,  # type: ignore
            on=self._get_on_labels(grouping),
            ignoring=self._get_ignoring_labels(grouping),
        )

    def _get_on_labels(self, grouping: Tree | None) -> Sequence[str]:
        if not grouping:
            return []
        if grouping.children[0].data == "on":  # type: ignore
            return self._get_labels(
                grouping.children[0].children[1].children  # type: ignore
            )
        return []

    def _get_ignoring_labels(self, grouping: Tree | None) -> Sequence[str]:
        if not grouping:
            return []
        if grouping.children[0].data == "ignoring":  # type: ignore
            return self._get_labels(
                grouping.children[0].children[1].children  # type: ignore
            )
        return []

    def _get_labels(self, labels: list[str | Tree]) -> Sequence[str]:
        result: list[str] = []
        for label_name in labels:
            result.append(label_name.value)  # type: ignore
        return result
