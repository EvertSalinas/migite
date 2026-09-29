#!/usr/bin/env python3
"""log_compact — shrink a tooling log to what an agent needs to fix the failures.

A mass-failure rspec run is mostly repetition: ~80% backtraces, and hundreds of
failure blocks that collapse to a handful of distinct errors (docs/improvements.md,
2026-09-28 pass: 606 examples, 224 failures, 795 KB — 15 unique bodies). Embedded
whole, that overflows the model's context window and the CLI rejects the heal call.

  python -m migite.log_compact rspec [--max-bytes N] FILE

Under --max-bytes the file passes through unchanged — small runs keep their full
backtraces. Over it, the output keeps every UNIQUE failure once (frames stripped
but the first, identical bodies merged with a count), plus the examples summary
and the full "Failed examples:" list; still over budget, the middle is elided
head+tail, the same shape as lib/stack.sh's truncate_log.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_MAX_BYTES = 60000

FAILURES_HEADER = re.compile(r"^Failures:\s*$")
BLOCK_HEADER = re.compile(r"^\s{1,3}\d+\) ")
FRAME_LINE = re.compile(r"^\s*#")
SUMMARY_LINE = re.compile(r"^\d+ examples?, ")
PROGRESS_LINE = re.compile(r"^[.F*E\s]+$")


def head_tail(text: str, max_bytes: int, path: str) -> str:
    """truncate_log's shape: first 60% + elision marker + last 40%, byte-based."""
    data = text.encode("utf-8")
    head = max_bytes * 3 // 5
    tail = max_bytes - head
    elided = len(data) - head - tail
    marker = f"\n\n... [{elided} bytes elided — full log: {path}] ...\n\n"
    return data[:head].decode("utf-8", "replace") + marker + data[-tail:].decode("utf-8", "replace")


def _parse_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """(header, body lines) per failure block in the Failures: section."""
    blocks: list[tuple[str, list[str]]] = []
    header, body = None, []
    for line in lines:
        if BLOCK_HEADER.match(line):
            if header is not None:
                blocks.append((header, body))
            header, body = line, []
        elif header is not None:
            body.append(line)
    if header is not None:
        blocks.append((header, body))
    return blocks


def compact_rspec(text: str, max_bytes: int, path: str) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    lines = text.splitlines()
    try:
        failures_at = next(i for i, l in enumerate(lines) if FAILURES_HEADER.match(l))
    except StopIteration:
        return head_tail(text, max_bytes, path)   # not an rspec failure log — byte-cap only
    summary_at = next((i for i in range(failures_at, len(lines)) if SUMMARY_LINE.match(lines[i])), len(lines))

    # The progress dots carry no information once the run is done.
    head = [l for l in lines[:failures_at] if not PROGRESS_LINE.match(l)]
    blocks = _parse_blocks(lines[failures_at + 1:summary_at])
    tail = lines[summary_at:]

    # Dedupe on the frameless body: the header (the spec's own description) and
    # the backtrace differ per example even when the error is the same.
    seen: dict[str, tuple[str, list[str], str, int]] = {}   # body -> (header, content, first frame, count)
    for header, body in blocks:
        content = [l for l in body if not FRAME_LINE.match(l)]
        first_frame = next((l for l in body if FRAME_LINE.match(l)), "")
        key = "\n".join(content)
        if key in seen:
            h, c, f, n = seen[key]
            seen[key] = (h, c, f, n + 1)
        else:
            seen[key] = (header, content, first_frame, 1)

    out = head
    out.append(f"Failures ({len(blocks)} total, {len(seen)} distinct — identical errors merged,"
               f" backtraces trimmed to the first frame; full log: {path}):")
    out.append("")
    for header, content, first_frame, count in seen.values():
        suffix = f"   [×{count} failures with this exact error]" if count > 1 else ""
        out.append(header.rstrip() + suffix)
        out.extend(content)
        if first_frame:
            out.append(first_frame)
        out.append("")
    out.extend(tail)

    result = "\n".join(out) + "\n"
    if len(result.encode("utf-8")) > max_bytes:
        result = head_tail(result, max_bytes, path)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="log_compact: shrink a tooling log for an agent prompt")
    ap.add_argument("kind", choices=["rspec"], help="log format to parse")
    ap.add_argument("file", help="the log to compact")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help=f"pass through unchanged up to this size (default: {DEFAULT_MAX_BYTES})")
    args = ap.parse_args()

    path = Path(args.file)
    text = path.read_text(encoding="utf-8", errors="replace")
    sys.stdout.write(compact_rspec(text, args.max_bytes, str(path)))


if __name__ == "__main__":
    main()
