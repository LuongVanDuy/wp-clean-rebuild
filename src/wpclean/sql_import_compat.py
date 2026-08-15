from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator


_CREATE_BLOCK_RE = re.compile(
    r"CREATE TABLE(?: IF NOT EXISTS)?\s+`?([A-Za-z0-9_]+)`?\s*\((.*?)\)\s*ENGINE\b[^;]*;",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN_RE = re.compile(r"^\s*`([^`]+)`\s+([A-Za-z]+)\b", re.MULTILINE)
_INSERT_RE = re.compile(
    r"^\s*(?:INSERT|REPLACE)\s+INTO\s+`?([A-Za-z0-9_]+)`?\s+VALUES\b",
    re.IGNORECASE,
)
_QUOTED_DECIMAL_RE = re.compile(r"^(\s*)'([0-9]+)'(\s*)$", re.DOTALL)


def _bit_column_indexes(sql: str) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for create in _CREATE_BLOCK_RE.finditer(sql):
        indexes: set[int] = set()
        for index, column in enumerate(_COLUMN_RE.finditer(create.group(2))):
            if column.group(2).lower() == "bit":
                indexes.add(index)
        if indexes:
            result[create.group(1)] = indexes
    return result


def _unquote_bit_token(token: str) -> tuple[str, bool]:
    match = _QUOTED_DECIMAL_RE.fullmatch(token)
    if not match:
        return token, False
    return f"{match.group(1)}{match.group(2)}{match.group(3)}", True


def _normalize_insert(statement: str, bit_indexes: set[int]) -> tuple[str, int]:
    match = _INSERT_RE.match(statement)
    if not match:
        return statement, 0

    replacements: list[tuple[int, int, str]] = []
    depth = 0
    field_index = 0
    field_start = -1
    in_quote = False
    index = match.end()
    length = len(statement)

    while index < length:
        char = statement[index]
        if in_quote:
            if char == "\\":
                index += 2
                continue
            if char == "'":
                if index + 1 < length and statement[index + 1] == "'":
                    index += 2
                    continue
                in_quote = False
            index += 1
            continue

        if char == "'":
            in_quote = True
        elif char == "(":
            depth += 1
            if depth == 1:
                field_index = 0
                field_start = index + 1
        elif char == "," and depth == 1:
            if field_index in bit_indexes and field_start >= 0:
                token, changed = _unquote_bit_token(statement[field_start:index])
                if changed:
                    replacements.append((field_start, index, token))
            field_index += 1
            field_start = index + 1
        elif char == ")" and depth == 1:
            if field_index in bit_indexes and field_start >= 0:
                token, changed = _unquote_bit_token(statement[field_start:index])
                if changed:
                    replacements.append((field_start, index, token))
            depth = 0
            field_start = -1
        elif char == ")" and depth > 1:
            depth -= 1
        index += 1

    if not replacements:
        return statement, 0

    normalized = statement
    for start, end, token in reversed(replacements):
        normalized = normalized[:start] + token + normalized[end:]
    return normalized, len(replacements)


def normalize_bit_literals(source: Path, destination: Path) -> int:
    """Write import-safe SQL without changing the verified clean SQL source."""
    source_text = source.read_text(encoding="utf-8", errors="replace")
    bit_columns = _bit_column_indexes(source_text)
    if not bit_columns:
        destination.write_text(source_text, encoding="utf-8", newline="\n")
        return 0

    output: list[str] = []
    pending: list[str] = []
    pending_bits: set[int] | None = None
    replacements = 0

    for line in source_text.splitlines(keepends=True):
        if pending_bits is None:
            insert = _INSERT_RE.match(line)
            table = insert.group(1) if insert else ""
            indexes = bit_columns.get(table)
            if not indexes:
                output.append(line)
                continue
            pending_bits = indexes
            pending = [line]
        else:
            pending.append(line)

        if line.rstrip().endswith(";"):
            statement, changed = _normalize_insert("".join(pending), pending_bits)
            output.append(statement)
            replacements += changed
            pending = []
            pending_bits = None

    if pending:
        statement, changed = _normalize_insert("".join(pending), pending_bits or set())
        output.append(statement)
        replacements += changed

    destination.write_text("".join(output), encoding="utf-8", newline="\n")
    return replacements


@contextmanager
def prepared_sql_for_import(source: Path) -> Iterator[tuple[Path, int]]:
    """Yield a temporary compatible SQL file when legacy BIT literals need repair."""
    descriptor, raw_path = tempfile.mkstemp(prefix="wpclean-import-ready-", suffix=".sql")
    os.close(descriptor)
    prepared = Path(raw_path)
    try:
        replacements = normalize_bit_literals(source, prepared)
        yield (prepared if replacements else source), replacements
    finally:
        prepared.unlink(missing_ok=True)


__all__ = ["normalize_bit_literals", "prepared_sql_for_import"]
