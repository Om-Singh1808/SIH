"""Hybrid Logical Clock (Kulkarni et al. 2014) for cross-device event ordering.

Every edge device stamps each event with ``hlc`` = ``"{physical_ms:013d}-{logical:04d}-{node}"``.
The string sorts lexicographically in causal order even when device wall clocks
disagree by a little, because:

* ``now()`` never goes backwards - if the physical clock stalls or steps back, the
  logical counter increments instead;
* ``receive(remote)`` merges a timestamp seen from another node (cloud command,
  MQTT message) so local time is at least as late as anything already observed.

Per-device ``seq`` remains the gap-free ordering used for sync; HLC is the
cross-device tiebreaker used by the cloud aggregator.
"""

import threading

from .clock import Clock, SystemClock

_MAX_LOGICAL = 9999


class HLC:
    def __init__(self, node: str, clock: Clock | None = None):
        if "-" in node:
            # The node id is the last field; dashes would break parsing of remote stamps.
            node = node.replace("-", "_")
        self.node = node
        self.clock = clock or SystemClock()
        self.physical = 0
        self.logical = 0
        self._lock = threading.Lock()

    # -- helpers -------------------------------------------------------------
    def _physical_now(self) -> int:
        return int(self.clock.now() * 1000)

    def _format(self) -> str:
        return f"{self.physical:013d}-{self.logical:04d}-{self.node}"

    @staticmethod
    def parse(stamp: str) -> tuple[int, int, str]:
        """Split a stamp into ``(physical_ms, logical, node)``."""
        phys, logical, node = stamp.split("-", 2)
        return int(phys), int(logical), node

    def _bump_logical(self) -> None:
        if self.logical >= _MAX_LOGICAL:
            self.physical += 1
            self.logical = 0
        else:
            self.logical += 1

    # -- API -----------------------------------------------------------------
    @property
    def last(self) -> str:
        return self._format()

    def now(self) -> str:
        """Issue a new local timestamp, strictly greater than any previous one."""
        with self._lock:
            pt = self._physical_now()
            if pt > self.physical:
                self.physical = pt
                self.logical = 0
            else:
                self._bump_logical()
            return self._format()

    def receive(self, remote: str) -> str:
        """Merge a remote stamp and issue a new local stamp greater than both."""
        r_phys, r_logical, _ = self.parse(remote)
        with self._lock:
            pt = self._physical_now()
            if pt > self.physical and pt > r_phys:
                self.physical = pt
                self.logical = 0
            elif self.physical == r_phys:
                self.logical = max(self.logical, r_logical)
                self._bump_logical()
            elif self.physical > r_phys:
                self._bump_logical()
            else:
                self.physical = r_phys
                self.logical = r_logical
                self._bump_logical()
            return self._format()

    def restore(self, stamp: str) -> None:
        """Re-seed from a persisted stamp (``device_state.hlc_last``) after restart."""
        phys, logical, _ = self.parse(stamp)
        with self._lock:
            if (phys, logical) > (self.physical, self.logical):
                self.physical, self.logical = phys, logical


__all__ = ["HLC"]
