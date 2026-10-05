"""Bounded decimal arithmetic: public inputs, no eval, network or model requests."""

import pytest

from app.services.errors import ServiceError
from app.tools.gateway import built_in_tools


@pytest.mark.parametrize("expression, value", [
    ("0.1 + 0.2", "0.3"),
    ("(125.5 - 5.5) * 3 / 2", "180"),
    ("-(-2) + +3", "5"),
    ("1 / 8", "0.125"),
])
async def test_registered_calculator_returns_actual_decimal_result(expression, value):
    tool = built_in_tools()["local.calculate"]
    arguments = tool.validate({"expression": expression})
    assert arguments == {"expression": expression}
    result = await tool.run(None, None, arguments)
    assert result["value"] == value
    assert result["expression"] == expression
    assert result["rounded"] is False
    assert tool.scope == "any" and tool.approval_required is False


async def test_calculator_marks_rounding_and_has_safe_zero_division():
    tool = built_in_tools()["local.calculate"]
    result = await tool.run(None, None, {"expression": "1 / 3"})
    assert result["rounded"] is True
    assert result["precision"] == 28
    with pytest.raises(ServiceError) as error:
        await tool.run(None, None, {"expression": "1 / (2 - 2)"})
    assert error.value.code == "calculation_zero_division"


async def test_calculator_does_not_inherit_other_decimal_flags():
    from decimal import Inexact, localcontext

    with localcontext() as context:
        context.flags[Inexact] = True
        result = await built_in_tools()["local.calculate"].run(None, None, {"expression": "1 + 2"})
        assert result["rounded"] is False


async def test_zero_divided_by_zero_has_specific_reason():
    with pytest.raises(ServiceError) as error:
        await built_in_tools()["local.calculate"].run(None, None, {"expression": "0 / 0"})
    assert error.value.code == "calculation_zero_division"


@pytest.mark.parametrize("arguments", [
    {}, {"expression": ""}, {"expression": True},
    {"expression": "1+2", "kb_id": "private"},
    {"expression": "__import__('os').system('echo no')"},
    {"expression": "open('private').read()"},
    {"expression": "[1, 2]"}, {"expression": "2 ** 100000"},
    {"expression": "1 // 2"}, {"expression": "0x10"},
    {"expression": "1e99"}, {"expression": "True + 1"},
    {"expression": "1 + "}, {"expression": "1" * 65},
    {"expression": "+" * 65 + "1"},
    {"expression": "1+" * 65 + "1"},
    {"expression": "(" * 40 + "1" + ")" * 40},
    {"expression": "1 " * 150},
])
def test_calculator_rejects_code_and_excessive_work(arguments):
    assert built_in_tools()["local.calculate"].validate(arguments) is None
