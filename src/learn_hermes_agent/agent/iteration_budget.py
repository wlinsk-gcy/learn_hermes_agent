from __future__ import annotations



class IterationBudget:
    """Tracks how many provider/tool loop iterations one agent turn may use."""

    def __init__(self, max_total: int):
        self.max_total = max(0, int(max_total))
        self._used = 0

    def consume(self) -> bool:
        """Consume one iteration. Return False if the budget is exhausted."""
        if self._used >= self.max_total:
            return False

        self._used += 1
        return True

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.max_total - self._used)

