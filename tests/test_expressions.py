"""Tests for gemia.expressions safe evaluator."""

import math
import pytest

from gemia.expressions import SafeEvaluator, ExprError, validate_expression


class TestSafeEvaluator:
    """Basic evaluation tests."""

    def test_literal_number(self):
        ev = SafeEvaluator()
        assert ev.eval("42") == 42
        assert ev.eval("3.14") == 3.14

    def test_arithmetic(self):
        ev = SafeEvaluator()
        assert ev.eval("2 + 3") == 5
        assert ev.eval("10 - 4") == 6
        assert ev.eval("3 * 4") == 12
        assert ev.eval("12 / 3") == 4.0
        assert ev.eval("2 ** 8") == 256

    def test_time_binding(self):
        ev = SafeEvaluator(time=0.5)
        assert ev.eval("time") == 0.5
        assert ev.eval("time * 2") == 1.0

    def test_custom_binding(self):
        ev = SafeEvaluator(width=1920, height=1080)
        assert ev.eval("width") == 1920
        assert ev.eval("width + height") == 3000
        assert ev.eval("width / 2") == 960.0

    def test_comparison(self):
        ev = SafeEvaluator()
        assert ev.eval("5 > 3") is True
        assert ev.eval("5 < 3") is False
        assert ev.eval("5 == 5") is True
        assert ev.eval("5 != 3") is True

    def test_logical(self):
        ev = SafeEvaluator()
        assert ev.eval("True and True") is True
        assert ev.eval("True and False") is False
        assert ev.eval("False or True") is True

    def test_whitelisted_math(self):
        ev = SafeEvaluator()
        assert ev.eval("abs(-5)") == 5
        assert ev.eval("round(3.7)") == 4
        assert ev.eval("floor(3.7)") == 3
        assert ev.eval("ceil(3.2)") == 4
        assert ev.eval("sqrt(16)") == 4.0
        assert abs(ev.eval("sin(0)") - 0.0) < 1e-6
        assert abs(ev.eval("cos(0)") - 1.0) < 1e-6

    def test_easing_functions(self):
        # Linear: 0→100 over t∈[0,1]
        ev = SafeEvaluator()
        assert ev.eval("linear(0, 100, 0.0)") == 0
        assert ev.eval("linear(0, 100, 0.5)") == 50
        assert ev.eval("linear(0, 100, 1.0)") == 100

        # Ease in quad: accelerates
        assert ev.eval("ease_in_quad(0, 100, 0.0)") == 0
        val_mid = ev.eval("ease_in_quad(0, 100, 0.5)")
        val_end = ev.eval("ease_in_quad(0, 100, 1.0)")
        assert val_mid < 50  # accelerates, so slower at t=0.5
        assert val_end == 100

        # Ease out quad: decelerates
        val_mid = ev.eval("ease_out_quad(0, 100, 0.5)")
        assert val_mid > 50  # decelerates, so faster at t=0.5

    def test_nested_function_calls(self):
        ev = SafeEvaluator()
        assert ev.eval("abs(sin(0))") == 0.0
        assert ev.eval("round(sqrt(16))") == 4
        assert ev.eval("max(3, 7, 2)") == 7
        assert ev.eval("min(3, 7, 2)") == 2

    def test_parentheses(self):
        ev = SafeEvaluator()
        assert ev.eval("(2 + 3) * 4") == 20
        assert ev.eval("2 + (3 * 4)") == 14

    def test_complex_animation_expr(self):
        """Real-world keyframe expression."""
        # Opacity bounces: 0.5 + 0.5 * sin(time * 4 * pi)
        ev = SafeEvaluator(time=0.125)  # quarter period
        result = ev.eval("0.5 + 0.5 * sin(time * 4 * 3.14159)")
        assert 0.9 < result < 1.0  # should be near 1.0


class TestSafeEvaluatorErrors:
    """Error handling tests."""

    def test_empty_expression(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("")
        assert exc_info.value.code == "E_EMPTY"

    def test_syntax_error_invalid_expr(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError):
            ev.eval("2 +") 

    def test_undefined_name(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("undefined_var + 1")
        assert exc_info.value.code == "E_UNSAFE"

    def test_forbidden_import_statement(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError):
            ev.eval("__import__('os')")

    def test_forbidden_eval(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("eval('1+1')")
        assert exc_info.value.code == "E_UNSAFE"

    def test_forbidden_exec(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("exec('x=1')")
        assert exc_info.value.code == "E_UNSAFE"

    def test_forbidden_attribute_access(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("time.__class__")
        assert exc_info.value.code == "E_UNSAFE"

    def test_forbidden_subscript(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("[1, 2, 3][0]")
        assert exc_info.value.code == "E_UNSAFE"

    def test_forbidden_lambda(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("lambda x: x + 1")
        assert exc_info.value.code == "E_UNSAFE"

    def test_forbidden_listcomp(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("[x * 2 for x in range(10)]")
        assert exc_info.value.code == "E_UNSAFE"

    def test_forbidden_unknown_function(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("dangerous_func(1)")
        assert exc_info.value.code == "E_UNSAFE"

    def test_division_by_zero(self):
        ev = SafeEvaluator()
        with pytest.raises(ExprError) as exc_info:
            ev.eval("1 / 0")
        assert exc_info.value.code == "E_RUNTIME"

    def test_clamped_easing_param(self):
        """Easing functions clamp t to [0,1]."""
        ev = SafeEvaluator()
        # t > 1 should clamp to 1
        assert ev.eval("linear(0, 100, 1.5)") == 100
        # t < 0 should clamp to 0
        assert ev.eval("linear(0, 100, -0.5)") == 0


class TestValidateExpression:
    """Integration test for validate_expression helper."""

    def test_valid_expression(self):
        is_valid, err = validate_expression("time * 2")
        assert is_valid is True
        assert err is None

    def test_invalid_expression(self):
        is_valid, err = validate_expression("__import__('os')")
        assert is_valid is False
        assert err is not None

    def test_empty_expression(self):
        is_valid, err = validate_expression("")
        assert is_valid is False
        assert err is not None
