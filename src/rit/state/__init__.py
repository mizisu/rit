from rit.state.models import (
    LoadingState,
    PR,
    PRComment,
    PRFile,
    PRLabel,
    PRReview,
    PRUser,
)


# Import PRStore lazily to avoid circular imports
def __getattr__(name: str):
    if name == "PRStore":
        from rit.state.store import PRStore

        return PRStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LoadingState",
    "PR",
    "PRComment",
    "PRFile",
    "PRLabel",
    "PRReview",
    "PRStore",
    "PRUser",
]
