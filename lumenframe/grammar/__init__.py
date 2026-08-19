"""Spatial grammar: where everything is, and what is on top of what.

Answers spatial questions about a lumenframe document in a form a model can act
on. A rendered frame can show a model *that* the title sits on the subject's
face; it cannot say which layer to move or by how much. This package supplies
that half — real positions, real coverage, and the vocabulary that turns
"move it left a bit" into a definite number.

Built for planners that cannot take video input at all (Claude, GPT), but the
gain is not about modality: a model that can see the frame still cannot address
it. Being able to name the layer and the amount is the point.

**ADD-ONLY.** Nothing here mutates a document. Edits are returned as LayerPatch
proposals and applied by :func:`lumenframe.ops.apply_layer_patch`, so there is
exactly one write path. The package consumes the public faces of ``model``,
``compile``, ``preview`` and ``compose.framing`` and is imported by none of them.

**Not a pixel-free path.** Extents are measured from each layer's rendered alpha
(see :mod:`lumenframe.grammar.extent` for why that beats a per-type extent
solver). Two things alpha cannot see — fields the renderer discarded, and
anything outside the canvas — are recovered from the document in
:mod:`lumenframe.grammar.blindspots` and reported as ``warnings``.
"""
from __future__ import annotations

from lumenframe.grammar.language import PhraseError, propose
from lumenframe.grammar.stage import DETAIL_LEVELS, render_text, stage_report

__all__ = [
    "DETAIL_LEVELS",
    "PhraseError",
    "propose",
    "render_text",
    "stage_report",
]
