"""Composable scene templates — params -> a list of LayerPatch op dicts.

A *template* is a pure function ``(**params) -> list[op_dict]``: it expands into
the exact same op vocabulary an agent (or the editor) would emit, so the
``apply_template`` op just feeds the result through the normal dispatch. No
template touches the document directly and none introduces a new op — they are
sugar that stamps out a common multi-layer arrangement (a lower-third, an intro
title card, …) in one call.

Each template is registered in :data:`TEMPLATES` keyed by name. Add a new one by
writing a function in this package and listing it here; ``apply_template`` and
the catalog pick it up automatically.

Conventions
-----------
* Templates return ops only — never raw layers — so every layer they create is
  validated by the standard ``add_layer`` path.
* Layer ids are made unique per call via a ``prefix`` (default derived from the
  template name) so the same template can be applied twice without id clashes.
* Times are in seconds (the doc is seconds-canonical).
"""
from __future__ import annotations

from typing import Any, Callable

from lumenframe.templates.lower_third import lower_third
from lumenframe.templates.intro import intro

Template = Callable[..., list[dict[str, Any]]]

#: name -> template function. The single source of truth consumed by
#: ``apply_template`` (lumenframe.ops) and the catalogue.
TEMPLATES: dict[str, Template] = {
    "lower_third": lower_third,
    "intro": intro,
}


def template_names() -> list[str]:
    """Sorted list of registered template names."""
    return sorted(TEMPLATES)


def expand_template(name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Expand template ``name`` with ``params`` into a list of op dicts.

    Raises:
        KeyError: if ``name`` is not a registered template.
    """
    fn = TEMPLATES[name]
    return fn(**(params or {}))


__all__ = ["TEMPLATES", "Template", "template_names", "expand_template", "lower_third", "intro"]
