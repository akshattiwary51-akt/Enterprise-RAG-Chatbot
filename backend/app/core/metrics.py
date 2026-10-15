"""Minimal in-process operational metrics.

Only counters and timing aggregates -- never prompts, contexts or document text.
Limitation: per-process; with several gunicorn workers each has its own view and
values reset on restart. Export to Prometheus/OTel for fleet-wide numbers.
"""
from __future__ import annotations

import threading
from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self._counters: dict[str, float] = defaultdict(float)
            self._timers: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # count,sum,max

    def inc(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, seconds: float) -> None:
        with self._lock:
            t = self._timers[name]
            t[0] += 1
            t[1] += seconds
            t[2] = max(t[2], seconds)

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            timers = {
                k: {
                    "count": int(v[0]),
                    "avg_ms": round(v[1] / v[0] * 1000, 2) if v[0] else 0.0,
                    "max_ms": round(v[2] * 1000, 2),
                }
                for k, v in self._timers.items()
            }
        answers = counters.get("rag_answers_total", 0)
        derived = {
            "no_answer_rate": round(counters.get("rag_no_answer_total", 0) / answers, 4) if answers else None,
            "citation_coverage": round(counters.get("rag_answers_with_valid_citation_total", 0) / answers, 4)
            if answers
            else None,
            "ingestion_failure_rate": _rate(counters, "ingestion_failure_total", "ingestion_success_total"),
        }
        return {"counters": counters, "timers": timers, "derived": derived, "scope": "process"}


def _rate(counters: dict, fail_key: str, ok_key: str):
    total = counters.get(fail_key, 0) + counters.get(ok_key, 0)
    return round(counters.get(fail_key, 0) / total, 4) if total else None


metrics = Metrics()
