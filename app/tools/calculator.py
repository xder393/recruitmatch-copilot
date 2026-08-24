"""计算器工具：基于 AST 白名单的安全表达式求值。"""

from __future__ import annotations

import ast
import math
import operator

from app.core.exceptions import ToolExecutionError

# 只允许这些运算符，杜绝任意代码执行
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "pow": pow,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
}


def _eval(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        return _BINARY_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ToolExecutionError("表达式包含不允许的语法")


def safe_calc(expression: str):
    """安全计算算术表达式，返回 int / float。非法输入抛 ToolExecutionError。"""
    expression = expression.strip()
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolExecutionError(f"表达式无法解析: {expression}") from exc

    try:
        result = _eval(tree.body)
    except ToolExecutionError:
        raise
    except (ZeroDivisionError, OverflowError, ValueError) as exc:
        raise ToolExecutionError(f"计算失败: {exc}") from exc

    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return result
