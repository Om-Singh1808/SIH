"""ULID generation without an external dependency.

A ULID is 128 bits: 48-bit millisecond timestamp + 80 bits of randomness, encoded
as 26 Crockford base32 characters.  Lexicographic order == creation order, which
is why event_id / alert_id / batch_id are ULIDs: SQLite primary keys stay
append-friendly and logs sort naturally.

Within the same millisecond the random part is *incremented* (monotonic ULID),
so ids generated in a tight loop on one process are still strictly sorted.
Passing an explicit ``ts`` encodes that timestamp faithfully (replay tooling,
deterministic fixtures); such ids are monotonic among themselves per ms.
"""

import secrets
import threading
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LOCK = threading.Lock()
_last_ms = -1
_last_rand = 0
_explicit_ms = -1
_explicit_rand = 0
_RAND_MASK = (1 << 80) - 1


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(ts: float | None = None) -> str:
    """Return a 26-char monotonic ULID. ``ts`` (epoch seconds) encodes that time instead of wall time."""
    global _last_ms, _last_rand, _explicit_ms, _explicit_rand
    if ts is not None:
        ms = int(ts * 1000)
        with _LOCK:
            if ms == _explicit_ms:
                _explicit_rand = (_explicit_rand + 1) & _RAND_MASK
            else:
                _explicit_ms = ms
                _explicit_rand = secrets.randbits(80)
            return _encode(ms, 10) + _encode(_explicit_rand, 16)
    ms = int(time.time() * 1000)
    with _LOCK:
        if ms <= _last_ms:
            # Same (or earlier, clock skew) millisecond: keep the time component
            # and bump the random tail so ordering stays strict.
            ms = _last_ms
            _last_rand = (_last_rand + 1) & _RAND_MASK
            if _last_rand == 0:  # astronomically unlikely overflow
                ms += 1
                _last_rand = secrets.randbits(80)
        else:
            _last_rand = secrets.randbits(80)
        _last_ms = ms
        rand = _last_rand
    return _encode(ms, 10) + _encode(rand, 16)


def ulid_timestamp(ulid: str) -> float:
    """Decode the millisecond timestamp of a ULID back to epoch seconds."""
    value = 0
    for ch in ulid[:10]:
        value = (value << 5) | CROCKFORD.index(ch.upper())
    return value / 1000.0


def is_ulid(s: str) -> bool:
    return isinstance(s, str) and len(s) == 26 and all(c in CROCKFORD for c in s.upper())


__all__ = ["CROCKFORD", "is_ulid", "new_ulid", "ulid_timestamp"]
