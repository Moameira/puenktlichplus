import time
from collections import deque


class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.calls: deque[float] = deque()

    def allow(self) -> bool:
        now = time.time()
        while self.calls and now - self.calls[0] > self.window_seconds:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(now)
        return True
