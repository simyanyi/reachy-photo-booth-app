"""Require fresh, centered person detections before a still photo."""

import math
import time


class PhotoGate:
    def __init__(self, max_age: float = 1.5, center_tolerance: float = 0.15):
        self.max_age = max_age
        self.center_tolerance = center_tolerance
        self.last_seen = float("-inf")
        self.frame_index = -1
        self.centered = False

    def clear(self) -> None:
        self.last_seen = float("-inf")
        self.centered = False

    def observe(self, detection, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        boxes = detection.bounding_boxes
        if not 0 <= detection.marker_id < len(boxes):
            self.clear()
            return
        box = boxes[detection.marker_id]
        values = (box.top_left_x, box.top_left_y, box.width, box.height, box.score)
        if not all(math.isfinite(v) for v in values) or min(
            box.width, box.height, box.score
        ) <= 0:
            self.clear()
            return
        # Replayed frames must not keep an old detection alive.
        if box.frame_index == self.frame_index or (
            box.frame_index < self.frame_index and now - self.last_seen <= self.max_age
        ):
            return
        self.frame_index = box.frame_index
        self.last_seen = now
        self.centered = (
            abs(box.top_left_x + box.width / 2 - 0.5) <= self.center_tolerance
            and abs(box.top_left_y + box.height / 2 - 0.5) <= self.center_tolerance
            and box.width * box.height >= 0.10
        )

    def ready(
        self,
        now: float | None = None,
        after: float | None = None,
        require_centered: bool = True,
    ) -> bool:
        now = time.monotonic() if now is None else now
        return (
            (self.centered or not require_centered)
            and 0 <= now - self.last_seen <= self.max_age
            and (after is None or self.last_seen >= after)
        )
