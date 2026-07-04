"""Director layer: read the timestamped footage table, decide the edit (Claude as editor).

Perception (VLM + Whisper + human notes) produces a timestamped table; this layer hands that
table to an LLM acting as the editor/director and turns its decisions back into an IntentionList
the render layer already understands. Pure halves (prompt build / reply parse / table render /
mapping) are unit-tested; the live LLM call is injected.
"""
