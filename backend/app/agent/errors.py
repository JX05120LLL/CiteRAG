"""Lightweight control signal, safe to import without optional dependencies."""


class AgentPaused(Exception):
    """The graph checkpoint and waiting attempt were committed; no answer yet."""


class AgentError(Exception):
    """Safe public error code only; never provider diagnostics."""
