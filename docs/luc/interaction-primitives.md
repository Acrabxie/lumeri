# Luc interaction primitives

The primitive layer describes what a person expresses, independently of the
widget used to collect it:

```text
semantic value → primitive → control/handle → renderer/surface
```

The source implementation currently provides:

- `scalar`: a finite value with an explicit unit, bounds, precision/step, and
  absolute or relative mode;
- `point2d`: a finite point with explicit coordinate space, origin, anchor,
  reference, and mode;
- `transform2d`: a point plus ratio scale, with optional rotation and anchor;
- `quad2d`: four independently named `point2d` values sharing one space,
  origin, reference, and mode;
- grouped values with deterministic serialization/digest and a host-owned
  renderer registry.

For the current `layout.adjust` source contract, the supported object is an
existing static LumenFrame `type=text` layer, identified through the current
Host target kind `lumenframe_text_layer`, with a center anchor and uniform
base scale. Position is an absolute point in `composition.frame.pixels`, with a
`center` origin and `center` anchor referenced to `composition.frame`. The
values are pixel offsets from the composition center, matching LumenFrame's
existing `transform.x/y` fields (zero means centered); they are not screen CSS
coordinates or normalized fractions. Scale is an absolute ratio in `[0.25, 2]`.
Its value maps directly to the existing `uniform_scale` edit field, which sets
both native `scale_x` and `scale_y`; it is not multiplied by the target's
current scale. Rotation is left unchanged. The Runtime's target inspector
(`LucInteractions._inspect_target`) refuses a target that is not a text layer,
is locked, sits under a transformed parent, is clipped to the layer below, or
has transform keyframes (`E_LUC_TARGET`, `E_LUC_TARGET_LOCKED`,
`E_LUC_TARGET_TRANSFORM_CONTEXT`, `E_LUC_TARGET_KEYFRAMED`). `emphasis` remains
proposal-only because no verified Project property mapping exists.

The Official `scalar.collect` and `quad2d.collect` capabilities collect
structured proposals without a Project write path. (`visual.treatment` is an
unbundled source package; the agent is not offered it.) The Host requires an
authenticated confirmation before it can mint an apply credential; in Runtime
1.1 that confirmation comes only from the creator's own route, never from the
agent. A confirmed `layout.adjust` proposal is applied through the existing
composition mutation gate. That does not add LumenFrame history or Undo for
the edit.

The renderer registry selects by primitive type, supported surface, registered
renderer capabilities, and an optional presentation hint. The hint is a
preference only. Packages do not register JavaScript or provide a renderer
implementation.

The canonical lifecycle contract models persistent human interaction states.
In Runtime 1.1 the agent's turn waits on the creator without polling the
model, and the same tool call then receives the result (`luc_interactions.py`,
`tests/test_runtime_luc_interactions.py`). The preview producer emits drafts
on the Session event stream. No Agent total-step cap or human-wait polling
loop was introduced.

The primitive schemas and each capability's output schema are the Host's own:
it validates results and picks renderers with them. The agent is given a
capability's input schema and not these; see
[agent-protocol.md](agent-protocol.md).

This document describes source behavior (2026-10-06). It is not an installed
Runtime or live Video acceptance record, and the model in every test of this
path is scripted.
