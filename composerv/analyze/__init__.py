"""Analysis layer. The fake backend auto-registers; real backends (qwen_mlx, claude_*)
register lazily on import so their heavy deps aren't pulled unless selected."""

from composerv.analyze.backends import claude_cli as _claude_cli  # noqa: F401  (registers "claude-cli")
from composerv.analyze.backends import fake as _fake  # noqa: F401  (registers "fake")
