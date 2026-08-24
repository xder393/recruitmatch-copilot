"""计算器工具：正确性 + 安全性。"""

from __future__ import annotations

import pytest

from app.core.exceptions import ToolExecutionError
from app.tools.calculator import safe_calc


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("2+3", 5),
        ("2+3*4", 14),
        ("(2+3)*4", 20),
        ("2**10", 1024),
        ("7//2", 3),
        ("7%3", 1),
        ("sqrt(9)", 3),
        ("10/4", 2.5),
        ("-3+1", -2),
    ],
)
def test_valid_arithmetic(expr, expected):
    assert safe_calc(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('ls')",
        "open('/etc/passwd')",
        "print('hello')",
        "x = 1",
        "1;2",
        "eval('2+2')",
    ],
)
def test_rejects_malicious(expr):
    with pytest.raises(ToolExecutionError):
        safe_calc(expr)


def test_rejects_division_by_zero():
    with pytest.raises(ToolExecutionError):
        safe_calc("1/0")
