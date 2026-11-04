"""Project hooks: the commands a project runs around the agent loop.

``HookRunner`` is the only entry point; it never imports ``rich`` or ``click``, and
it reports through the ``note`` and ``warn`` callbacks the CLI passes in, the same
way the MCP manager does.
"""

from nar.hooks.runner import HookOutcome, HookRunner

__all__ = ["HookOutcome", "HookRunner"]
