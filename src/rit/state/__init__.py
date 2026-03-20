from rit.state.models import (
    PR,
    PRFile,
    PRComment,
    PRReview,
    PRUser,
    PRLabel,
    LoadingState,
)


# Import PRStore lazily to avoid circular imports
def __getattr__(name: str):
    if name == "PRStore":
        from rit.state.store import PRStore

        return PRStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PR",
    "PRFile",
    "PRComment",
    "PRReview",
    "PRUser",
    "PRLabel",
    "LoadingState",
    "PRStore",
]
