"""Errors with explicit retry semantics."""


class WorkflowError(Exception):
    """A permanent error, safe to display to the user."""


class TransientError(WorkflowError):
    """An operation that may succeed when retried."""
