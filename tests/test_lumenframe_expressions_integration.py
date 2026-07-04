"""Integration tests for lumenframe expressions (set_expression op + render).

Tests that expressions bind correctly, are validated, and evaluate during rendering.
"""

import numpy as np
import pytest

from lumenframe import (
    apply_layer_patch,
    empty_doc,
    find_layer,
)
from lumenframe.compile import compile_to_layer_stack
from gemia.expressions import validate_expression


class TestSetExpressionOp:
    """Tests for set_expression op registration and application."""

    def test_set_expression_creates_binding(self):
        """set_expression op creates expression binding on layer."""
        doc = empty_doc()
        
        # Create doc and add a layer via op (which assigns proper ID)
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "add_layer",
                    "parent_id": "root",
                    "type": "solid",
                    "color": "#FF0000",
                }
            ],
        }
        doc = apply_layer_patch(doc, patch)
        
        # Get the created layer ID
        root = doc["root"]
        layer_id = root["children"][0]["id"]
        
        # Apply set_expression op
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "set_expression",
                    "layer_id": layer_id,
                    "property": "opacity",
                    "expression": "time * 2",
                }
            ],
        }
        new_doc = apply_layer_patch(doc, patch)
        
        layer = find_layer(new_doc, layer_id)
        assert layer is not None
        assert "expressions" in layer
        assert "opacity" in layer["expressions"]
        assert layer["expressions"]["opacity"]["expr"] == "time * 2"

    def test_set_expression_validates_safety(self):
        """set_expression rejects unsafe expressions."""
        doc = empty_doc()
        
        # Add a layer
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "add_layer",
                    "parent_id": "root",
                    "type": "solid",
                }
            ],
        }
        doc = apply_layer_patch(doc, patch)
        layer_id = doc["root"]["children"][0]["id"]
        
        # Try to set unsafe expression
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "set_expression",
                    "layer_id": layer_id,
                    "property": "opacity",
                    "expression": "__import__('os')",  # Forbidden
                }
            ],
        }
        
        with pytest.raises(Exception) as exc_info:
            apply_layer_patch(doc, patch)
        assert "E_UNSAFE" in str(exc_info.value)

    def test_set_expression_multiple_properties(self):
        """Can set expressions on different properties of the same layer."""
        doc = empty_doc()
        
        patch = {
            "version": 1,
            "ops": [
                {"op": "add_layer", "parent_id": "root", "type": "solid"}
            ],
        }
        doc = apply_layer_patch(doc, patch)
        layer_id = doc["root"]["children"][0]["id"]
        
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "set_expression",
                    "layer_id": layer_id,
                    "property": "opacity",
                    "expression": "time",
                },
                {
                    "op": "set_expression",
                    "layer_id": layer_id,
                    "property": "transform.rotation",
                    "expression": "time * 90",
                },
            ],
        }
        new_doc = apply_layer_patch(doc, patch)
        
        layer = find_layer(new_doc, layer_id)
        assert len(layer["expressions"]) == 2
        assert "opacity" in layer["expressions"]
        assert "transform.rotation" in layer["expressions"]


class TestExpressionRenderIntegration:
    """Tests that expressions are evaluated during render."""

    def test_opacity_expression_animates(self):
        """Opacity expression changes over time during render."""
        # Create doc with expression on opacity
        doc = empty_doc()
        doc["canvas"] = {"width": 640, "height": 360, "fps": 24}
        
        # Add red solid layer
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "add_layer",
                    "parent_id": "root",
                    "type": "solid",
                    "color": "#FF0000",
                    "start": 0,
                    "duration": 2.0,
                    "opacity": 1.0,
                }
            ],
        }
        doc = apply_layer_patch(doc, patch)
        layer_id = doc["root"]["children"][0]["id"]
        
        # Bind opacity expression: varies 0→1→0 over 2 seconds
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "set_expression",
                    "layer_id": layer_id,
                    "property": "opacity",
                    "expression": "0.5 + 0.5 * sin(time * 3.14159)",
                }
            ],
        }
        doc = apply_layer_patch(doc, patch)
        
        # Compile and render
        stack = compile_to_layer_stack(doc)
        
        # At frame 0 (t=0), opacity should be ~0.5 (sin(0)=0)
        opacity_frame0 = stack.layers[0].property_value("opacity", 0, 24)
        assert 0.45 < opacity_frame0 < 0.55, f"Expected ~0.5, got {opacity_frame0}"
        
        # At frame 12 (t=0.5s), opacity should be ~1.0 (sin(pi/2)=1)
        opacity_frame_mid = stack.layers[0].property_value("opacity", 12, 24)
        assert 0.95 < opacity_frame_mid < 1.05, f"Expected ~1.0, got {opacity_frame_mid}"
        
        # At frame 24 (t=1s), opacity should be ~0.5 (sin(pi)=0)
        opacity_frame_end = stack.layers[0].property_value("opacity", 24, 24)
        assert 0.45 < opacity_frame_end < 0.55, f"Expected ~0.5, got {opacity_frame_end}"

    def test_rotation_expression_animates(self):
        """Rotation expression applies correctly during render."""
        doc = empty_doc()
        doc["canvas"] = {"width": 640, "height": 360, "fps": 24}
        
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "add_layer",
                    "parent_id": "root",
                    "type": "solid",
                    "start": 0,
                    "duration": 1.0,
                }
            ],
        }
        doc = apply_layer_patch(doc, patch)
        layer_id = doc["root"]["children"][0]["id"]
        
        # Bind rotation: 0 → 360 degrees over 1 second
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "set_expression",
                    "layer_id": layer_id,
                    "property": "transform.rotation",
                    "expression": "time * 360",
                }
            ],
        }
        doc = apply_layer_patch(doc, patch)
        
        stack = compile_to_layer_stack(doc)
        
        # At frame 0, rotation should be ~0
        rot_frame0 = stack.layers[0].property_value("transform.rotation", 0, 24)
        assert rot_frame0 < 5, f"Expected ~0, got {rot_frame0}"
        
        # At frame 12 (t=0.5s), rotation should be ~180
        rot_frame_mid = stack.layers[0].property_value("transform.rotation", 12, 24)
        assert 175 < rot_frame_mid < 185, f"Expected ~180, got {rot_frame_mid}"


class TestExpressionPrecedence:
    """Test precedence: expressions > keyframes > static values."""

    def test_expression_overrides_static_value(self):
        """Expression binding takes precedence over static property value."""
        doc = empty_doc()
        doc["canvas"] = {"width": 640, "height": 360, "fps": 24}
        
        # Create layer with static opacity 0.3
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "add_layer",
                    "parent_id": "root",
                    "type": "solid",
                    "opacity": 0.3,
                    "start": 0,
                    "duration": 1.0,
                }
            ],
        }
        doc = apply_layer_patch(doc, patch)
        layer_id = doc["root"]["children"][0]["id"]
        
        # Bind expression: opacity = 0.7
        patch = {
            "version": 1,
            "ops": [
                {
                    "op": "set_expression",
                    "layer_id": layer_id,
                    "property": "opacity",
                    "expression": "0.7",
                }
            ],
        }
        doc = apply_layer_patch(doc, patch)
        
        stack = compile_to_layer_stack(doc)
        
        # At any frame, opacity should be 0.7 (from expression), not 0.3 (static)
        opacity = stack.layers[0].property_value("opacity", 0, 24)
        assert abs(opacity - 0.7) < 0.01, f"Expected 0.7, got {opacity}"


class TestExpressionSafety:
    """Verify expression safety guarantees."""

    def test_validate_expression_rejects_import(self):
        """validate_expression rejects import statements."""
        is_valid, err = validate_expression("import os")
        assert not is_valid
        assert err is not None

    def test_validate_expression_rejects_getattr(self):
        """validate_expression rejects getattr (attribute access)."""
        is_valid, err = validate_expression("getattr(time, '__class__')")
        assert not is_valid

    def test_validate_expression_allows_math(self):
        """validate_expression allows math operations."""
        is_valid, err = validate_expression("sin(time) + cos(time)")
        assert is_valid
        assert err is None

    def test_validate_expression_allows_easing(self):
        """validate_expression allows easing functions."""
        is_valid, err = validate_expression("ease_in_quad(0, 100, time)")
        assert is_valid
        assert err is None
