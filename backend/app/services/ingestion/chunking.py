"""Parent/child chunking.

Child chunks (small) are embedded and searched; each points at a parent passage (larger window)
that is what the LLM actually reads. Spreadsheet rows are never split across chunks (unless a
single row alone exceeds the chunk size) and never mix sheets.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..retrieval.vectorstore import ChunkRecord
from .parsers import Segment


@dataclass
class ParentSpec:
    id: str
    ordinal: int
    text: str
    locator: dict = field(default_factory=dict)


def _row_range(rows: list[int]) -> str:
    return f"{rows[0]}-{rows[-1]}" if rows[0] != rows[-1] else str(rows[0])


def build_chunks(
    segments: list[Segment], document_id: str, index_version: int, chunk_size: int, chunk_overlap: int,
    parent_size: int,
) -> tuple[list[ParentSpec], list[ChunkRecord]]:
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    parent_splitter = RecursiveCharacterTextSplitter(chunk_size=parent_size, chunk_overlap=0)

    parents: list[ParentSpec] = []
    chunks: list[ChunkRecord] = []

    def add_parent(text: str, locator: dict) -> ParentSpec:
        p = ParentSpec(id=str(uuid.uuid4()), ordinal=len(parents), text=text, locator=locator)
        parents.append(p)
        return p

    def add_child(parent: ParentSpec, text: str, locator: dict) -> None:
        ordinal = len(chunks)
        chunks.append(
            ChunkRecord(
                chunk_id=f"{document_id}:{index_version}:{ordinal}",
                document_id=document_id,
                index_version=index_version,
                parent_id=parent.id,
                ordinal=ordinal,
                text=text,
                locator=locator,
            )
        )

    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg.kind == "text":
            pieces = parent_splitter.split_text(seg.text)
            for part_no, piece in enumerate(pieces, start=1):
                loc = dict(seg.locator)
                if len(pieces) > 1:
                    loc["part"] = part_no
                parent = add_parent(piece, loc)
                for child in child_splitter.split_text(piece):
                    add_child(parent, child, loc)
            i += 1
            continue

        # consecutive rows of the same sheet -> parent windows
        sheet = seg.locator["sheet"]
        run: list[Segment] = []
        while i < len(segments) and segments[i].kind == "row" and segments[i].locator["sheet"] == sheet:
            run.append(segments[i])
            i += 1
        window: list[Segment] = []
        size = 0

        def flush_window():
            nonlocal window, size
            if not window:
                return
            rows = [s.locator["row"] for s in window]
            ploc = {"sheet": sheet, "rows": _row_range(rows)}
            parent = add_parent("\n".join(s.text for s in window), ploc)
            pack: list[Segment] = []
            psize = 0

            def flush_pack():
                nonlocal pack, psize
                if pack:
                    prow = [s.locator["row"] for s in pack]
                    add_child(parent, "\n".join(s.text for s in pack), {"sheet": sheet, "rows": _row_range(prow)})
                pack, psize = [], 0

            for s in window:
                if len(s.text) > chunk_size:  # very wide single row: split, keep provenance
                    flush_pack()
                    for piece in child_splitter.split_text(s.text):
                        add_child(parent, piece, {"sheet": sheet, "rows": str(s.locator["row"])})
                    continue
                if psize + len(s.text) + 1 > chunk_size and pack:
                    flush_pack()
                pack.append(s)
                psize += len(s.text) + 1
            flush_pack()
            window, size = [], 0

        for s in run:
            if size + len(s.text) + 1 > parent_size and window:
                flush_window()
            window.append(s)
            size += len(s.text) + 1
        flush_window()
    return parents, chunks
