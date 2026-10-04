"""Revision-bound request state shared by PR overview sections."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from rit.state.models import LoadingState


@dataclass
class OverviewResource[K, T]:
    """Own one section's loading lifecycle and reject superseded responses."""

    value: T | None = None
    loading: LoadingState = LoadingState.IDLE
    error: str | None = None
    _key: K | None = field(default=None, init=False, repr=False)
    _request: int = field(default=0, init=False, repr=False)

    @property
    def loaded_value(self) -> T | None:
        """Expose a snapshot only after a successful load."""
        return self.value if self.loading == LoadingState.LOADED else None

    async def load(
        self,
        *,
        current_key: Callable[[], K],
        fetch: Callable[[K], Awaitable[T]],
        on_change: Callable[[], None],
        refresh: bool = False,
    ) -> None:
        """Load once per key, preserving newer state through failure or cancellation."""
        key = current_key()
        if self._key == key and (
            self.loading == LoadingState.LOADING
            or (self.loading == LoadingState.LOADED and not refresh)
        ):
            return
        self._request += 1
        request = self._request
        self._key = key
        self.loading = LoadingState.LOADING
        self.error = None
        on_change()
        try:
            value = await fetch(key)
        except RuntimeError as error:
            if not self._is_current(request, current_key()):
                return
            self.loading = LoadingState.ERROR
            self.error = str(error)
        except asyncio.CancelledError:
            if request == self._request:
                self.loading = LoadingState.IDLE
                on_change()
            raise
        else:
            if not self._is_current(request, current_key()):
                return
            self.value = value
            self.loading = LoadingState.LOADED
        on_change()

    def _is_current(self, request: int, key: K) -> bool:
        return request == self._request and key == self._key
