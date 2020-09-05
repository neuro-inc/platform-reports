import enum
import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

from antlr4 import CommonTokenStream, InputStream, ParseTreeWalker
from antlr4.error.ErrorListener import ErrorListener
from antlr4.ParserRuleContext import ParserRuleContext
from antlr4.Recognizer import Recognizer
from antlr4.Token import Token
from antlr4.tree.Tree import ParseTree

from .promql_ast.PromQLLexer import PromQLLexer
from .promql_ast.PromQLParser import PromQLParser
from .promql_ast.PromQLParserListener import PromQLParserListener


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


def parse_query_metrics(query: str) -> Sequence[Metric]:
    ast = parse_expression_ast(query)
    listener = MetricListener()
    walk_expression_ast(listener, ast)
    return listener.metrics


class PromQLErrorListener(ErrorListener):
    def syntaxError(
        self,
        recognizer: Recognizer,
        offending_symbol: Token,
        line: int,
        column: int,
        message: str,
        e: Optional[Exception],
    ) -> None:
        raise PromQLException(
            f"{line}:{column}: error at {offending_symbol.text!r}: {message}"
        )


class MetricListener(PromQLParserListener):
    def __init__(self) -> None:
        self._metrics: List[Metric] = []

    @property
    def metrics(self) -> Sequence[Metric]:
        return self._metrics

    def exitInstantSelector(self, ctx: PromQLParser.InstantSelectorContext) -> None:
        metric_name_ctx = ctx.METRIC_NAME()
        metric_name = metric_name_ctx.getText() if metric_name_ctx else ""
        self._metrics.append(
            Metric(name=metric_name, label_matchers=self._get_label_matchers(ctx))
        )

    def _get_operator_ctx(
        self, ctx: PromQLParser.VectorOperationContext
    ) -> ParserRuleContext:
        return (
            ctx.powOp()
            or ctx.multOp()
            or ctx.addOp()
            or ctx.compareOp()
            or ctx.andUnlessOp()
            or ctx.orOp()
        )

    def _get_label_matchers(
        self, ctx: PromQLParser.InstantSelectorContext
    ) -> Dict[str, LabelMatcher]:
        list_ctx: PromQLParser.LabelMatcherListContext = ctx.labelMatcherList()
        if not list_ctx:
            return {}
        result: Dict[str, LabelMatcher] = {}
        for matcher in list_ctx.labelMatcher():
            assert isinstance(matcher, PromQLParser.LabelMatcherContext)
            name = matcher.labelName().getText()
            result[name] = LabelMatcher(
                name=name,
                operator=LabelMatcherOperator(matcher.labelMatcherOperator().getText()),
                value=matcher.STRING().getText()[1:-1],
            )
        return result


def parse_expression_ast(expression: str) -> ParseTree:
    error_listener = PromQLErrorListener()
    parser = PromQLParser(CommonTokenStream(PromQLLexer(InputStream(expression))))
    parser.removeErrorListeners()
    parser.addErrorListener(error_listener)
    return parser.expression()


def walk_expression_ast(listener: PromQLParserListener, ast: ParseTree) -> None:
    walker = ParseTreeWalker()
    walker.walk(listener, ast)
