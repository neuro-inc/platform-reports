parser grammar PromQLParser
    ;

options {
    tokenVocab = PromQLLexer;
}

expression: vectorOperation EOF;

// Binary operations are ordered by precedence

// Unary operations have the same precedence as multiplications

vectorOperation
    : <assoc = right> vectorOperation powOp vectorOperation
    | unaryOp vectorOperation
    | vectorOperation multOp vectorOperation
    | vectorOperation addOp vectorOperation
    | vectorOperation compareOp vectorOperation
    | vectorOperation andUnlessOp vectorOperation
    | vectorOperation orOp vectorOperation
    | vector
    ;

// Operators

unaryOp:     (ADD | SUB);
powOp:       POW grouping?;
multOp:      (MULT | DIV | MOD) grouping?;
addOp:       (ADD | SUB) grouping?;
compareOp:   (DEQ | NE | GT | LT | GE | LE) BOOL? grouping?;
andUnlessOp: (AND | UNLESS) grouping?;
orOp:        OR grouping?;

vector
    : function
    | aggregation
    | instantSelector
    | matrixSelector
    | offset
    | literal
    | parens
    ;

parens: LEFT_PAREN vectorOperation RIGHT_PAREN;

// Selectors

instantSelector
    : METRIC_NAME (LEFT_BRACE labelMatcherList? RIGHT_BRACE)?
    | LEFT_BRACE labelMatcherList RIGHT_BRACE
    ;

labelMatcher:         labelName labelMatcherOperator STRING;
labelMatcherOperator: EQ | NE | RE | NRE;
labelMatcherList:     labelMatcher (COMMA labelMatcher)*;

matrixSelector: instantSelector TIME_RANGE;

offset: instantSelector OFFSET DURATION | matrixSelector OFFSET DURATION;

// Functions

function
    : FUNCTION LEFT_PAREN vectorOperation (COMMA vectorOperation)* RIGHT_PAREN
    ;

// Aggregations

aggregation
    : AGGREGATION_OPERATOR parameterList
    | AGGREGATION_OPERATOR (by | without) parameterList
    | AGGREGATION_OPERATOR parameterList (by | without)
    ;
parameterList
    : LEFT_PAREN (vectorOperation (COMMA vectorOperation)*)? RIGHT_PAREN
    ;
by:      BY labelNameList;
without: WITHOUT labelNameList;

// Vector one-to-one/one-to-many joins

grouping:   (on | ignoring) (groupLeft | groupRight)?;
on:         ON labelNameList;
ignoring:   IGNORING labelNameList;
groupLeft:  GROUP_LEFT labelNameList;
groupRight: GROUP_RIGHT labelNameList;

// Label names

labelName:     keyword | LABEL_NAME;
labelNameList: LEFT_PAREN (labelName (COMMA labelName)*)? RIGHT_PAREN;

keyword
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
    | AGGREGATION_OPERATOR
    | FUNCTION
    ;

literal: NUMBER | STRING;