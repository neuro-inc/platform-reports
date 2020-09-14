PROMQL = """
start: vector_expression

// Binary operations are defined separately in order to support precedence

?vector_expression: or_join

?or_join\
    : and_unless_join
    | or_join OR grouping? and_unless_join

?and_unless_join\
    : comparison_join
    | and_unless_join (AND | UNLESS) grouping? comparison_join

?comparison_join\
    : sum_join
    | comparison_join /==|!=|>=|<=|>|</ BOOL? grouping? sum_join

?sum_join\
    : product_join
    | sum_join /\+|-/ grouping? product_join

?product_join\
    : unary
    | product_join /\*|\/|%/ grouping? unary

?unary\
    : power_join
    | /\+|-/ power_join

?power_join\
    : atom
    | atom /\^/ grouping? power_join

?atom\
    : vector
    | "(" vector_expression ")"

?vector\
    : function
    | aggregation
    | instant_selector
    | matrix_selector
    | offset
    | NUMBER
    | STRING

// Selectors

instant_selector\
    : METRIC_NAME ("{" label_matcher_list? "}")? -> instant_selector_with_metric
    | "{" label_matcher_list "}" -> instant_selector_without_metric

label_matcher_list: label_matcher ("," label_matcher)*
label_matcher: label_name /=~|=|!=|!~/ STRING

matrix_selector: instant_selector time_range
time_range: "[" DURATION "]" | "[" DURATION ":" DURATION? "]"

offset\
    : instant_selector OFFSET DURATION
    | matrix_selector OFFSET DURATION

// Function

function: function_name parameter_list
parameter_list: "(" (vector_expression ("," vector_expression)*)? ")"
?function_name\
    : ABS
    | ABSENT
    | ABSENT_OVER_TIME
    | CEIL
    | CHANGES
    | CLAMP_MAX
    | CLAMP_MIN
    | DAY_OF_MONTH
    | DAY_OF_WEEK
    | DAYS_IN_MONTH
    | DELTA
    | DERIV
    | EXP
    | FLOOR
    | HISTOGRAM_QUANTILE
    | HOLT_WINTERS
    | HOUR
    | IDELTA
    | INCREASE
    | IRATE
    | LABEL_JOIN
    | LABEL_REPLACE
    | LN
    | LOG2
    | LOG10
    | MINUTE
    | MONTH
    | PREDICT_LINEAR
    | RATE
    | RESETS
    | ROUND
    | SCALAR
    | SORT
    | SORT_DESC
    | SQRT
    | TIME
    | TIMESTAMP
    | VECTOR
    | YEAR
    | AVG_OVER_TIME
    | MIN_OVER_TIME
    | MAX_OVER_TIME
    | SUM_OVER_TIME
    | COUNT_OVER_TIME
    | QUANTILE_OVER_TIME
    | STDDEV_OVER_TIME
    | STDVAR_OVER_TIME

// Aggregations

aggregation\
    : aggregation_operator parameter_list
    | aggregation_operator (by | without) parameter_list
    | aggregation_operator parameter_list (by | without)
by: BY label_name_list
without: WITHOUT label_name_list
?aggregation_operator\
    : SUM
    | MIN
    | MAX
    | AVG
    | GROUP
    | STDDEV
    | STDVAR
    | COUNT
    | COUNT_VALUES
    | BOTTOMK
    | TOPK
    | QUANTILE

// Vector one-to-one/one-to-many joins

grouping: (on | ignoring) (group_left | group_right)?
on: ON label_name_list
ignoring: IGNORING label_name_list
group_left: GROUP_LEFT label_name_list
group_right: GROUP_RIGHT label_name_list

// Label names

label_name_list: "(" (label_name ("," label_name)*)? ")"
?label_name: keyword | LABEL_NAME

?keyword\
    : AND
    | OR
    | UNLESS
    | BY
    | WITHOUT
    | ON
    | IGNORING
    | GROUP_LEFT
    | GROUP_RIGHT
    | OFFSET
    | BOOL
    | aggregation_operator
    | function_name

// Keywords

// Function names

ABS: "abs"
ABSENT: "absent"
ABSENT_OVER_TIME: "absent_over_time"
CEIL: "ceil"
CHANGES: "changes"
CLAMP_MAX: "clamp_max"
CLAMP_MIN: "clamp_min"
DAY_OF_MONTH: "day_of_month"
DAY_OF_WEEK: "day_of_week"
DAYS_IN_MONTH: "days_in_month"
DELTA: "delta"
DERIV: "deriv"
EXP: "exp"
FLOOR: "floor"
HISTOGRAM_QUANTILE: "histogram_quantile"
HOLT_WINTERS: "holt_winters"
HOUR: "hour"
IDELTA: "idelta"
INCREASE: "increase"
IRATE: "irate"
LABEL_JOIN: "label_join"
LABEL_REPLACE: "label_replace"
LN: "ln"
LOG2: "log2"
LOG10: "log10"
MINUTE: "minute"
MONTH: "month"
PREDICT_LINEAR: "predict_linear"
RATE: "rate"
RESETS: "resets"
ROUND: "round"
SCALAR: "scalar"
SORT: "sort"
SORT_DESC: "sort_desc"
SQRT: "sqrt"
TIME: "time"
TIMESTAMP: "timestamp"
VECTOR: "vector"
YEAR: "year"
AVG_OVER_TIME: "avg_over_time"
MIN_OVER_TIME: "min_over_time"
MAX_OVER_TIME: "max_over_time"
SUM_OVER_TIME: "sum_over_time"
COUNT_OVER_TIME: "count_over_time"
QUANTILE_OVER_TIME: "quantile_over_time"
STDDEV_OVER_TIME: "stddev_over_time"
STDVAR_OVER_TIME: "stdvar_over_time"

// Aggregation operators

SUM: "sum"
MIN: "min"
MAX: "max"
AVG: "avg"
GROUP: "group"
STDDEV: "stddev"
STDVAR: "stdvar"
COUNT: "count"
COUNT_VALUES: "count_values"
BOTTOMK: "bottomk"
TOPK: "topk"
QUANTILE: "quantile"

// Aggregation modifiers

BY: "by"
WITHOUT: "without"

// Join modifiers

ON: "on"
IGNORING: "ignoring"
GROUP_LEFT: "group_left"
GROUP_RIGHT: "group_right"

// Logical operators

AND: "and"
OR: "or"
UNLESS: "unless"

OFFSET: "offset"

BOOL: "bool"

NUMBER: /[0-9]+(\.[0-9]+)?/

STRING\
    : "'" /([^'\\\\]|\\\\.)*/ "'"
    | "\\"" /([^\\"\\\\]|\\\\.)*/ "\\""

DURATION: DIGIT+ ("s" | "m" | "h" | "d" | "w" | "y")

METRIC_NAME: (LETTER | "_" | ":") (DIGIT | LETTER | "_" | ":")*

LABEL_NAME: (LETTER | "_") (DIGIT | LETTER | "_")*

%import common.DIGIT
%import common.LETTER
%import common.WS

%ignore WS
"""  # noqa: W605
