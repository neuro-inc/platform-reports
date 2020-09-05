lexer grammar PromQLLexer
    ;

NUMBER: [0-9]+ ('.' [0-9]+)?;

STRING
    : '\'' (~('\'' | '\\') | '\\' .)* '\''
    | '"' (~('"' | '\\') | '\\' .)* '"'
    ;

// Binary operators

ADD:  '+';
SUB:  '-';
MULT: '*';
DIV:  '/';
MOD:  '%';
POW:  '^';

AND:    'and';
OR:     'or';
UNLESS: 'unless';

// Comparison operators

EQ:  '=';
DEQ: '==';
NE:  '!=';
GT:  '>';
LT:  '<';
GE:  '>=';
LE:  '<=';
RE:  '=~';
NRE: '!~';

// Aggregation modifiers

BY:      'by';
WITHOUT: 'without';

// Join modifiers

ON:          'on';
IGNORING:    'ignoring';
GROUP_LEFT:  'group_left';
GROUP_RIGHT: 'group_right';

OFFSET: 'offset';

BOOL: 'bool';

AGGREGATION_OPERATOR
    : 'sum'
    | 'min'
    | 'max'
    | 'avg'
    | 'group'
    | 'stddev'
    | 'stdvar'
    | 'count'
    | 'count_values'
    | 'bottomk'
    | 'topk'
    | 'quantile'
    ;

FUNCTION
    : 'abs'
    | 'absent'
    | 'absent_over_time'
    | 'ceil'
    | 'changes'
    | 'clamp_max'
    | 'clamp_min'
    | 'day_of_month'
    | 'day_of_week'
    | 'days_in_month'
    | 'delta'
    | 'deriv'
    | 'exp'
    | 'floor'
    | 'histogram_quantile'
    | 'holt_winters'
    | 'hour'
    | 'idelta'
    | 'increase'
    | 'irate'
    | 'label_join'
    | 'label_replace'
    | 'ln'
    | 'log2'
    | 'log10'
    | 'minute'
    | 'month'
    | 'predict_linear'
    | 'rate'
    | 'resets'
    | 'round'
    | 'scalar'
    | 'sort'
    | 'sort_desc'
    | 'sqrt'
    | 'time'
    | 'timestamp'
    | 'vector'
    | 'year'
    | 'avg_over_time'
    | 'min_over_time'
    | 'max_over_time'
    | 'sum_over_time'
    | 'count_over_time'
    | 'quantile_over_time'
    | 'stddev_over_time'
    | 'stdvar_over_time'
    ;

LEFT_BRACE:  '{';
RIGHT_BRACE: '}';

LEFT_PAREN:  '(';
RIGHT_PAREN: ')';

LEFT_BRACKET:  '[';
RIGHT_BRACKET: ']';

COMMA: ',';

TIME_RANGE
    : LEFT_BRACKET DURATION RIGHT_BRACKET
    | LEFT_BRACKET DURATION ':' DURATION? RIGHT_BRACKET
    ;

DURATION: [0-9]+ ('s' | 'm' | 'h' | 'd' | 'w' | 'y');

METRIC_NAME: [a-zA-Z_:] [a-zA-Z0-9_:]*;
LABEL_NAME:  [a-zA-Z_] [a-zA-Z0-9_]*;

WS: [\r\t\n ]+ -> skip;