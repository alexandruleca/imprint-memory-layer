"""Imprint – public API surface."""

def workspace_stats(workspace: str) -> dict:
    # Importing lightweight helpers must not require the whole model stack.
    from imprint.vectorstore import workspace_stats as stats
    return stats(workspace)

__all__ = ["workspace_stats"]
