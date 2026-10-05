"""Small bounded decimal expressions; never execute Python or access external data."""

import ast
import re
from decimal import Context, Decimal, DecimalException, DivisionByZero, Inexact, localcontext

from app.services.errors import ServiceError

INPUT_SCHEMA = {
    "type": "object",
    "properties": {"expression": {
        "type": "string", "minLength": 1, "maxLength": 256,
        "description": "十进制数字、括号与 + - * /；不支持函数、幂或科学计数法。",
    }},
    "required": ["expression"],
    "additionalProperties": False,
}


def _parse(expression: str) -> ast.Expression:
    if not 1 <= len(expression) <= 256 or not re.fullmatch(r"[0-9.()+*/\- \t]+", expression):
        raise ValueError("Unsupported expression")
    depth = 0
    for character in expression:
        depth += (character == "(") - (character == ")")
        if depth > 32:
            raise ValueError("Expression nesting limit")
    tree = ast.parse(expression, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > 64:
        raise ValueError("Expression work limit")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub)
    for node in nodes:
        if not isinstance(node, allowed):
            raise ValueError("Unsupported expression node")
        if isinstance(node, ast.Constant):
            literal = ast.get_source_segment(expression, node)
            if (literal is None or len(literal) > 64
                or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", literal)):
                raise ValueError("Unsupported decimal literal")
    return tree


def validate(arguments: dict) -> dict | None:
    if (not isinstance(arguments, dict) or set(arguments) != {"expression"}
        or not isinstance(arguments["expression"], str)):
        return None
    expression = arguments["expression"].strip()
    try:
        _parse(expression)
    except (ValueError, SyntaxError, RecursionError):
        return None
    return {"expression": expression}


async def calculate(_session, _context, arguments: dict) -> dict:
    checked = validate(arguments)
    if checked is None:
        raise ServiceError(422, "tool_arguments_invalid", "只支持有界的十进制四则运算")
    expression = checked["expression"]
    tree = _parse(expression)

    def evaluate(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant):
            return Decimal(ast.get_source_segment(expression, node))
        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand)
            return -value if isinstance(node.op, ast.USub) else +value
        left, right = evaluate(node.left), evaluate(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if right == 0:
            raise ServiceError(422, "calculation_zero_division", "除数不能为零，请检查表达式")
        return left / right

    try:
        with localcontext(Context(prec=28, Emax=128, Emin=-128)) as decimal_context:
            value = evaluate(tree.body)
            text = format(value, "f")
            if len(text) > 256:
                raise ServiceError(422, "calculation_limit", "计算结果超过支持范围")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return {"expression": expression, "value": "0" if value == 0 else text,
                    "precision": 28, "rounded": decimal_context.flags[Inexact]}
    except DivisionByZero:
        raise ServiceError(422, "calculation_zero_division", "除数不能为零，请检查表达式") from None
    except DecimalException:
        raise ServiceError(422, "calculation_limit", "计算结果超过支持范围") from None
