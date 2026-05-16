from __future__ import annotations

from collections import defaultdict
from typing import Any


class EarningsTracker:
    def __init__(self) -> None:
        self.cache: dict[str, float] = defaultdict(float)
        self._last_notified: dict[str, float] = defaultdict(float)

    def add(self, node_name: str, usd_value: float) -> None:
        self.cache[node_name] += float(usd_value)

    def has_changed_since_last_notify(self, threshold: float = 0.0001) -> bool:
        for node, val in self.cache.items():
            if abs(val - self._last_notified.get(node, 0.0)) > threshold:
                return True
        return False

    def mark_notified(self) -> None:
        self._last_notified = dict(self.cache)

    def snapshot(self) -> dict[str, Any]:
        all_nodes = ["grass", "nodepay", "rivalz", "dawn", "gradient", "teneo", "openloop"]
        result: dict[str, Any] = {n: self.cache.get(n, 0.0) for n in all_nodes}
        result["total_usd"] = sum(self.cache.values())
        return result
