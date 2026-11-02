"""Pure drip-feed maths (unit-testable)."""
from __future__ import annotations


def build_segments(o: dict, speed: dict) -> list[dict]:
    """Segments run one after another: [{n units, d seconds}]."""
    svc, qty = o["service"], int(o["qty"])
    if svc == "views" and o.get("splits"):
        segs = []
        for v, m in o["splits"]:
            v, m = int(v), int(m)
            segs.append({"n": v, "d": m * 60 if m > 0 else max(10, int(v * float(speed["views"])))})
        return segs
    return [{"n": qty, "d": int(min(max(qty * float(speed.get(svc, 5)), 10), 24 * 3600))}]


def due_count(segs: list[dict], started_at: float, now: float, qty: int) -> int:
    """How many units should have been delivered by `now` (the first one is due immediately)."""
    t = now - started_at
    if t < 0:
        return 0
    total = 0
    for s in segs:
        if t >= s["d"]:
            total += s["n"]
            t -= s["d"]
        else:
            total += min(s["n"], int(s["n"] * t / s["d"]) + 1)
            break
    return min(total, qty)


def wait_hint(segs: list[dict], started_at: float, now: float) -> float:
    """Seconds to sleep until roughly the next unit becomes due."""
    t = now - started_at
    for s in segs:
        if t < s["d"]:
            return min(30.0, max(2.0, s["d"] / max(1, s["n"])))
        t -= s["d"]
    return 5.0
