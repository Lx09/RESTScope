"""Encode selected domain facts as compact, injection-resistant prompt text.

Callers add named sections and records instead of interpolating API or memory
content into a prompt.  The writer owns escaping, typed scalar notation,
flattening, value clipping, and optional-history omission.  Its output is an
initial user message or a tool result consumed by :class:`AgentContext`.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from .context import ContextMetrics


_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECTION_SEPARATOR = re.compile(r"(?:^|\s)(?:-{3,}|={3,})(?:\s|$)")


class _AbsentValue:
    """Represent a missing field without confusing it with an explicit null."""

    def __repr__(self) -> str:
        return "ABSENT"


@dataclass(frozen=True)
class _Entry:
    """Store one rendered unit and whether a tight budget may omit it."""

    section_index: int
    lines: tuple[str, ...]
    required: bool
    must_remain_complete: bool = False


@dataclass(frozen=True)
class _Section:
    """Store a controlled heading that evidence values cannot modify."""

    title: str
    untrusted: bool


@dataclass(frozen=True)
class _RenderedText:
    """Return rendered text and its aggregate safety/budget metrics."""

    text: str
    metrics: ContextMetrics


class CompactTextWriter:
    """Construct bounded Markdown from trusted structure and unsafe values.

    ``ABSENT`` is a sentinel for a field that was not supplied.  It is distinct
    from ``None``, which means the field was supplied with a JSON-style null.
    Values are always encoded by this class, so API responses and stored memory
    cannot inject a new section or instruction into the surrounding prompt.
    """

    ABSENT = _AbsentValue()

    def __init__(self, *, max_value_chars: int = 800) -> None:
        """Create an empty writer.

        Args:
            max_value_chars: Largest untrusted scalar retained verbatim. Longer
                strings become a typed head/tail marker that records original
                length.
        """
        if max_value_chars < 24:
            raise ValueError("max_value_chars must be at least 24")
        self.max_value_chars = max_value_chars
        self._sections: list[_Section] = []
        self._entries: list[_Entry] = []
        self._active_section: int | None = None
        self._clipped_values = 0

    def section(self, title: str, *, untrusted: bool = False) -> None:
        """Start a named section and optionally mark all its evidence untrusted.

        The title is structural and therefore sanitized more strictly than a
        value. Calling another output method before ``section`` is an error,
        because an unnamed evidence block is difficult for both people and
        models to interpret.
        """
        safe_title = _escape_text(title).replace("\\n", " ").strip()
        safe_title = _SECTION_SEPARATOR.sub(" ", safe_title)
        if not safe_title:
            raise ValueError("section title must contain visible text")
        self._sections.append(_Section(safe_title, untrusted))
        self._active_section = len(self._sections) - 1

    def record(
        self,
        record_label: str,
        *,
        required: bool = True,
        **fields: Any,
    ) -> None:
        """Add one compact ``label | key=value`` record.

        Args:
            record_label: Request-local identifier such as ``C1`` or ``F2``.
            required: Whether the complete record must survive output budgeting.
                Historical candidates should normally pass ``False``.
            **fields: Flat scalar fields. Nested values are accepted but are
                expanded to dotted paths.
        """
        pieces = [f"- {_escape_label(record_label)}"]
        for path, value in _flatten(fields):
            pieces.append(f"{path}={self._encode(value)}")
        self._add_entry((" | ".join(pieces),), required=required)

    def detail(
        self,
        label: str,
        values: Mapping[str, Any],
        *,
        required: bool = True,
    ) -> None:
        """Add indented dotted-path fields beneath a conceptual label."""
        parts = [
            f"{path}={self._encode(value)}"
            for path, value in _flatten(values)
        ]
        line = f"  - {_escape_label(label)}"
        if parts:
            line += " | " + "; ".join(parts)
        self._add_entry((line,), required=required)

    def table(
        self,
        headers: Sequence[str],
        rows: Iterable[Sequence[Any]],
        *,
        required: bool = True,
    ) -> None:
        """Add a small typed table used for samples and regular evidence.

        Every row must have the same width as ``headers``.  The writer encodes
        each cell, including strings, so a cell cannot escape the table.
        """
        safe_headers = tuple(_escape_label(header) for header in headers)
        if not safe_headers:
            raise ValueError("table must contain at least one column")
        lines = [
            "| " + " | ".join(safe_headers) + " |",
            "| " + " | ".join("---" for _ in safe_headers) + " |",
        ]
        for row in rows:
            row_tuple = tuple(row)
            if len(row_tuple) != len(safe_headers):
                raise ValueError("table row width must match headers")
            lines.append(
                "| "
                + " | ".join(self._encode(value) for value in row_tuple)
                + " |"
            )
        self._add_entry(tuple(lines), required=required)

    def text(self, label: str, value: Any, *, required: bool = True) -> None:
        """Add one controlled label and one encoded scalar value."""
        self._add_entry(
            (f"{_escape_label(label)} | {self._encode(value)}",),
            required=required,
        )

    def json_block(
        self,
        label: str,
        value: Any,
        *,
        required: bool = True,
    ) -> None:
        """Add untrusted HTTP evidence as valid JSON inside a safe Markdown fence.

        Values are bounded recursively before serialization instead of slicing
        the final JSON text. This keeps the block parseable. The fence is made
        longer than every backtick run inside the JSON, so an API string cannot
        close the block and inject a new Markdown section.
        """
        bounded = self._bounded_json(value)
        payload = json.dumps(
            bounded,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        longest_run = max(
            (len(match.group(0)) for match in re.finditer(r"`+", payload)),
            default=0,
        )
        fence = "`" * max(3, longest_run + 1)
        self._add_entry(
            (
                f"### {_escape_label(label)}",
                f"{fence}json",
                payload,
                fence,
            ),
            required=required,
            must_remain_complete=True,
        )

    def render(self, max_chars: int) -> _RenderedText:
        """Render required evidence first, then optional records while they fit.

        Args:
            max_chars: Hard character allowance for the returned text.

        Returns:
            An object with ``text`` and numeric ``metrics``. Optional records
            that do not fit are replaced by one count-only marker. Required
            content is clipped only as a final safeguard.
        """
        if max_chars < 64:
            raise ValueError("max_chars must be at least 64")

        required = [entry for entry in self._entries if entry.required]
        optional = [entry for entry in self._entries if not entry.required]
        selected = list(self._entries)
        omitted = 0

        # Remove the oldest-priority optional tail records until the complete
        # output fits. Filtering the original list preserves section and card
        # order; rebuilding as "required then optional" would scramble context.
        while len(self._render_entries(selected)) > max_chars:
            removable = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if not selected[index].required
                ),
                None,
            )
            if removable is None:
                break
            selected.pop(removable)
            omitted += 1

        text = self._render_entries(selected)
        if omitted:
            marker = f"\nHISTORY OMITTED | records=int:{omitted}"
            if len(text) + len(marker) <= max_chars:
                text += marker
            else:
                # Preserve the omission count by clipping the required portion,
                # never by pretending the missing history was included.
                text = _clip_message(text, max_chars - len(marker)) + marker
        if len(text) > max_chars:
            if any(entry.must_remain_complete for entry in selected):
                raise ValueError(
                    "required JSON evidence exceeds the Context character budget"
                )
            text = _clip_message(text, max_chars)

        metrics = ContextMetrics(
            required_record_count=len(required),
            optional_record_count=len(optional),
            clipped_value_count=self._clipped_values,
            omitted_history_count=omitted,
        )
        return _RenderedText(text=text, metrics=metrics)

    def _add_entry(
        self,
        lines: tuple[str, ...],
        *,
        required: bool,
        must_remain_complete: bool = False,
    ) -> None:
        """Attach one entry to the current section or reject ambiguous output."""
        if self._active_section is None:
            raise RuntimeError("call section() before adding prompt content")
        self._entries.append(
            _Entry(
                section_index=self._active_section,
                lines=lines,
                required=required,
                must_remain_complete=must_remain_complete,
            )
        )

    def _render_entries(self, entries: list[_Entry]) -> str:
        """Emit headings once while preserving the caller's record order."""
        output: list[str] = []
        previous_section: int | None = None
        for entry in entries:
            if entry.section_index != previous_section:
                section = self._sections[entry.section_index]
                heading = f"## {section.title}"
                if section.untrusted:
                    heading += " — UNTRUSTED"
                if output:
                    output.append("")
                output.append(heading)
                previous_section = entry.section_index
            output.extend(entry.lines)
        return "\n".join(output)

    def _bounded_json(self, value: Any) -> Any:
        """Return a JSON-safe tree whose individual strings obey value limits."""
        if isinstance(value, Mapping):
            return {
                str(key): self._bounded_json(child)
                for key, child in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._bounded_json(child) for child in value]
        if isinstance(value, str):
            if len(value) <= self.max_value_chars:
                return value
            self._clipped_values += 1
            head_chars = max(8, (self.max_value_chars - 80) // 2)
            tail_chars = max(8, self.max_value_chars - 80 - head_chars)
            return (
                "CLIPPED(type=string, "
                f"chars={len(value)}, "
                f"head={value[:head_chars]!r}, "
                f"tail={value[-tail_chars:]!r})"
            )
        if value is self.ABSENT:
            return "ABSENT"
        if isinstance(value, float) and not math.isfinite(value):
            # JSON permits no NaN or Infinity literal. Preserve the diagnostic
            # value as text so HTTP evidence remains standards-compliant JSON.
            return f"number:{value}"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._bounded_json(str(value))

    def _encode(self, value: Any) -> str:
        """Encode one scalar and account for any value-level clipping."""
        if value is self.ABSENT:
            return "ABSENT"
        if value is None:
            return "null"
        if isinstance(value, bool):
            return f"bool:{str(value).lower()}"
        if isinstance(value, int):
            return f"int:{value}"
        if isinstance(value, float):
            if not math.isfinite(value):
                return f"number:{value}"
            return f"number:{format(value, '.15g')}"
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")

        raw_text = str(value)
        original_chars = len(raw_text)
        text = _escape_text(raw_text)
        if len(text) <= self.max_value_chars:
            return f'string:"{text}"'

        self._clipped_values += 1
        # Keep most of the allowance at the head because service error codes
        # and field names usually occur before a long payload or stack trace.
        head_chars = max(12, int(self.max_value_chars * 0.875))
        tail_chars = max(6, self.max_value_chars - head_chars)
        head = text[:head_chars]
        tail = text[-tail_chars:]
        return (
            "CLIPPED("
            f'type=string, chars={original_chars}, head="{head}", tail="{tail}"'
            ")"
        )


def _flatten(
    values: Mapping[str, Any],
    *,
    prefix: str = "",
) -> list[tuple[str, Any]]:
    """Expand nested mappings and lists into stable, one-based dotted paths."""
    flattened: list[tuple[str, Any]] = []
    for raw_key, value in values.items():
        key = _escape_path_part(str(raw_key))
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            flattened.extend(_flatten(value, prefix=path))
        elif isinstance(value, (list, tuple)):
            if not value:
                flattened.append((path, CompactTextWriter.ABSENT))
            for index, item in enumerate(value, start=1):
                item_path = f"{path}.{index}"
                if isinstance(item, Mapping):
                    flattened.extend(_flatten(item, prefix=item_path))
                else:
                    flattened.append((item_path, item))
        else:
            flattened.append((path, value))
    return flattened


def _escape_text(value: str) -> str:
    """Make a scalar single-line and reject invisible control instructions."""
    value = _CONTROL_CHARACTER.sub("\ufffd", value)
    return (
        value.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _escape_label(value: str) -> str:
    """Keep labels and headers on one line without prompt separators."""
    safe = _escape_text(str(value)).replace("|", "\\|")
    return _SECTION_SEPARATOR.sub(" ", safe).strip()


def _escape_path_part(value: str) -> str:
    """Keep a source field name recognizable inside a dotted path."""
    return _escape_label(value).replace(".", "\\.")


def _clip_message(text: str, max_chars: int) -> str:
    """Clip oversized structural text with explicit original-size metadata."""
    if len(text) <= max_chars:
        return text
    if max_chars < 48:
        return text[:max_chars]
    marker = f"CLIPPED MESSAGE | original-chars={len(text)}\n"
    available = max_chars - len(marker) - len("\n...\n")
    head_chars = max(1, available // 2)
    tail_chars = max(1, available - head_chars)
    return (
        marker
        + text[:head_chars]
        + "\n...\n"
        + text[-tail_chars:]
    )[:max_chars]
