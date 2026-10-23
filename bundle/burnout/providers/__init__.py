"""Provider package (trimmed for quote0-monitor).

Only the codex provider is vendored — the other upstream providers
(claude/agy/deepseek/opencode) were removed with the merge; monitor.py
calls codex.get_usage / get_codex_reset_credits / build_codex_snapshot.
"""
