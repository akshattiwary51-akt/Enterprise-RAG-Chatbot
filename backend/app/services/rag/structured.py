"""Deterministic aggregate answers over spreadsheet/CSV documents.

Vector similarity cannot add up a column. For questions such as "total salary for Engineering"
we compute the figure exactly, in code, from the document's own cells.

Safety properties:
  * Runs ONLY on documents the caller has already been authorised for (the RAG service passes
    in revalidated documents) -- results enter the LLM context like any other authorised passage.
  * Never evaluates user text: the question is only *matched* against header names and cell
    values; the only operations are the fixed set below. No SQL, Python eval, or formulas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..ingestion.parsers import iter_sheet_rows

OPS = [
    (r"\b(total|sum)\b", "sum"),
    (r"\b(average|avg|mean)\b", "mean"),
    (r"\b(maximum|max|highest|largest|biggest)\b", "max"),
    (r"\b(minimum|min|lowest|smallest)\b", "min"),
    (r"\b(count|how many|number of)\b", "count"),
]
_NUM_RE = re.compile(r"^\(?-?[$€£₹]?\s*\(?-?[\d,]*\.?\d+\)?%?$")
MAX_UNIQUE_FILTER_VALUES = 500


@dataclass
class StructuredResult:
    text: str
    locator: dict


def _to_decimal(raw: str) -> Decimal | None:
    s = raw.strip()
    if not s or not _NUM_RE.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    digits = re.sub(r"[^\d.]", "", s)
    try:
        d = Decimal(digits)
    except InvalidOperation:
        return None
    return -d if neg else d


def _word_in(needle: str, haystack: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle.lower())}(?!\w)", haystack) is not None


def _fmt(d: Decimal) -> str:
    q = d.quantize(Decimal("0.01")) if d != d.to_integral_value() else d.quantize(Decimal(1))
    return f"{q:,}"


def detect_operation(question: str) -> str | None:
    q = question.lower()
    for pattern, op in OPS:
        if re.search(pattern, q):
            return op
    return None


def try_structured(question: str, path: Path, file_type: str) -> list[StructuredResult]:
    op = detect_operation(question)
    if op is None:
        return []
    q = question.lower()
    results: list[StructuredResult] = []
    try:
        sheets = list(iter_sheet_rows(path, file_type))
    except Exception:
        return []

    for sheet, rows in sheets:
        rows = [(n, r) for n, r in rows if any(c.strip() for c in r)]
        if len(rows) < 2:
            continue
        headers = [h.strip() or f"Column{i + 1}" for i, h in enumerate(rows[0][1])]
        data = [r + [""] * (len(headers) - len(r)) for _, r in rows[1:]]
        cols = {h: [row[i] for row in data] for i, h in enumerate(headers)}

        numeric_cols = []
        for h, vals in cols.items():
            non_empty = [v for v in vals if v.strip()]
            if non_empty and sum(1 for v in non_empty if _to_decimal(v) is not None) / len(non_empty) >= 0.8:
                numeric_cols.append(h)

        # choose aggregated column: longest header name mentioned in the question
        target = None
        if op != "count":
            mentioned = [h for h in numeric_cols if _word_in(h, q)]
            target = max(mentioned, key=len) if mentioned else None
            if target is None:
                continue

        # equality filters from categorical values named in the question
        filters: dict[str, str] = {}
        for h, vals in cols.items():
            if h == target or h in numeric_cols:
                continue
            uniq = {v.strip() for v in vals if v.strip()}
            if not uniq or len(uniq) > MAX_UNIQUE_FILTER_VALUES:
                continue
            hits = [v for v in uniq if len(v) >= 2 and _word_in(v, q)]
            if hits:
                filters[h] = max(hits, key=len)
        if op == "count" and not filters:
            continue

        keep = [
            i for i in range(len(data))
            if all(cols[h][i].strip().lower() == v.lower() for h, v in filters.items())
        ]
        if not keep:
            continue
        flt = ", ".join(f"{h} = {v}" for h, v in filters.items()) or "none"
        if op == "count":
            body = f"COUNT of rows = {len(keep)}"
        else:
            nums = [d for i in keep if (d := _to_decimal(cols[target][i])) is not None]
            if not nums:
                continue
            value = {"sum": sum(nums), "mean": sum(nums) / len(nums), "max": max(nums), "min": min(nums)}[op]
            body = f"{op.upper()} of {target} = {_fmt(Decimal(value))} (over {len(nums)} values)"
        text = f'Exact computation from sheet "{sheet}" (filters: {flt}): {body}.'
        results.append(StructuredResult(text=text, locator={"sheet": sheet, "computed": True}))
    return results
