"""Small in-process TTL cache for retailer collection results and coverage."""
from time import monotonic
from copy import deepcopy

from ..domain import Listing


class ListingTTLCache:
    def __init__(self):
        self._values: dict[str, tuple[float, list[Listing], dict]] = {}

    def get(self, key: str, ttl_seconds: float) -> list[Listing] | None:
        cached = self._values.get(key)
        if cached is None or ttl_seconds <= 0 or monotonic() - cached[0] >= ttl_seconds:
            self._values.pop(key, None)
            return None
        return [listing.model_copy(deep=True) for listing in cached[1]]

    def get_with_metadata(
        self, key: str, ttl_seconds: float
    ) -> tuple[list[Listing], dict] | None:
        cached = self._values.get(key)
        if cached is None or ttl_seconds <= 0 or monotonic() - cached[0] >= ttl_seconds:
            self._values.pop(key, None)
            return None
        return ([listing.model_copy(deep=True) for listing in cached[1]], deepcopy(cached[2]))

    def set(self, key: str, listings: list[Listing], metadata: dict | None = None) -> None:
        self._values[key] = (
            monotonic(),
            [listing.model_copy(deep=True) for listing in listings],
            deepcopy(metadata or {}),
        )
