"""Render selected domain facts as safe, bounded, readable Markdown.

Workflow code decides which facts matter and adds them as sections, cards,
details, tables, or HTTP JSON evidence. ``CompactTextWriter`` then applies one
project-wide presentation: JSON-style scalar values, recursive Markdown for
nested values, value clipping, and whole-card history omission. Its output is
an initial user message or tool result consumed by :class:`AgentContext`.
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
_INLINE_SEQUENCE_ITEMS = 8
_INLINE_SEQUENCE_CHARS = 240


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
                strings become a readable head/tail preview that records the
                original character count.
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
        """Add one self-contained Markdown card.

        Args:
            record_label: Request-local identifier such as ``C1`` or ``F2``.
            required: Whether the complete record must survive output budgeting.
                Historical candidates should normally pass ``False``.
            **fields: Scalar or nested facts. Nested mappings and collections
                remain nested instead of becoming dotted implementation paths.
        """
        lines = [f"- {_code_label(record_label)}"]
        for key, value in fields.items():
            lines.extend(
                self._named_value_lines(
                    key,
                    value,
                    indent=2,
                    capitalize=False,
                )
            )
        self._add_entry(tuple(lines), required=required)

    def detail(
        self,
        label: str,
        values: Any,
        *,
        required: bool = True,
    ) -> None:
        """Add a named recursive Markdown tree without dotted field paths.

        ``values`` is normally a mapping, but a workflow may supply a sequence
        when the label itself already names the collection, such as “affected
        inputs”. This keeps list structure explicit without adding another
        public Writer method.
        """
        lines = [f"{_display_label(label, capitalize=True)}:"]
        lines.extend(self._value_lines(values, indent=0))
        self._add_entry(tuple(lines), required=required)

    def table(
        self,
        headers: Sequence[str],
        rows: Iterable[Sequence[Any]],
        *,
        required: bool = True,
    ) -> None:
        """Add a small Markdown table used for samples and regular evidence.

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
                + " | ".join(self._table_cell(value) for value in row_tuple)
                + " |"
            )
        self._add_entry(tuple(lines), required=required)

    def text(self, label: str, value: Any, *, required: bool = True) -> None:
        """Add one controlled label with a scalar or recursive value."""
        self._add_entry(
            tuple(
                self._named_value_lines(
                    label,
                    value,
                    indent=0,
                    capitalize=True,
                    bullet=False,
                )
            ),
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
            noun = "record" if omitted == 1 else "records"
            marker = (
                "\n\n> "
                f"{omitted} optional history {noun} omitted to fit the context "
                "budget."
            )
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
                output.append("")
                previous_section = entry.section_index
            elif output and output[-1] != "":
                output.append("")
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
            head_chars, tail_chars = _preview_lengths(self.max_value_chars)
            return (
                f"{value[:head_chars]} … {value[-tail_chars:]} "
                f"[clipped from {len(value)} characters]"
            )
        if value is self.ABSENT:
            return "<not supplied>"
        if isinstance(value, float) and not math.isfinite(value):
            # JSON permits no NaN or Infinity literal. Preserve the diagnostic
            # value as text so HTTP evidence remains standards-compliant JSON.
            return str(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._bounded_json(str(value))

    def _encode_scalar(self, value: Any) -> str:
        """Encode one leaf as JSON-style text and account for clipping."""
        if value is self.ABSENT:
            return "<not supplied>"
        if value is None:
            return "null"
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if not math.isfinite(value):
                return json.dumps(str(value), ensure_ascii=False)
            return format(value, ".15g")
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")

        raw_text = str(value)
        original_chars = len(raw_text)
        safe_text = _clean_scalar(raw_text)
        if len(safe_text) <= self.max_value_chars:
            return json.dumps(safe_text, ensure_ascii=False)

        self._clipped_values += 1
        head_chars, tail_chars = _preview_lengths(self.max_value_chars)
        preview = f"{safe_text[:head_chars]} … {safe_text[-tail_chars:]}"
        return (
            f"{json.dumps(preview, ensure_ascii=False)} "
            f"(clipped from {original_chars} characters)"
        )

    def _named_value_lines(
        self,
        label: str,
        value: Any,
        *,
        indent: int,
        capitalize: bool,
        bullet: bool = True,
    ) -> list[str]:
        """Render one trusted field name and any scalar or nested value."""
        prefix = " " * indent + ("- " if bullet else "")
        safe_label = _display_label(label, capitalize=capitalize)
        if (
            _is_scalar(value)
            or self._can_inline_sequence(value)
            or isinstance(value, Mapping) and not value
        ):
            return [f"{prefix}{safe_label}: {self._inline_value(value)}"]

        lines = [f"{prefix}{safe_label}:"]
        child_indent = indent + (2 if bullet else 0)
        lines.extend(self._value_lines(value, indent=child_indent))
        return lines

    def _value_lines(self, value: Any, *, indent: int) -> list[str]:
        """Recursively render a collection while preserving source order."""
        if isinstance(value, Mapping):
            return self._mapping_lines(value, indent=indent)
        if isinstance(value, (list, tuple)):
            if not value:
                return [" " * indent + "[]"]
            lines: list[str] = []
            for item in value:
                if _is_scalar(item) or self._can_inline_sequence(item):
                    lines.append(
                        " " * indent + f"- {self._inline_value(item)}"
                    )
                    continue
                lines.append(" " * indent + "-")
                lines.extend(self._value_lines(item, indent=indent + 2))
            return lines
        return [" " * indent + self._encode_scalar(value)]

    def _mapping_lines(
        self,
        values: Mapping[Any, Any],
        *,
        indent: int,
    ) -> list[str]:
        """Render a mapping as nested field bullets or an explicit empty map."""
        if not values:
            return [" " * indent + "{}"]
        lines: list[str] = []
        for key, value in values.items():
            lines.extend(
                self._named_value_lines(
                    str(key),
                    value,
                    indent=indent,
                    capitalize=False,
                )
            )
        return lines

    def _can_inline_sequence(self, value: Any) -> bool:
        """Return whether a short leaf-only sequence is clearer on one line."""
        if not isinstance(value, (list, tuple)):
            return False
        if len(value) > _INLINE_SEQUENCE_ITEMS:
            return False
        if not all(_is_scalar(item) for item in value):
            return False
        # This check must not call the encoder because encoding records clipping
        # metrics. The selected sequence is encoded exactly once when rendered.
        if any(len(str(item)) > self.max_value_chars for item in value):
            return False
        estimated_chars = 2 + sum(len(str(item)) + 2 for item in value)
        return estimated_chars <= _INLINE_SEQUENCE_CHARS

    def _inline_value(self, value: Any) -> str:
        """Render a scalar or already-approved short scalar sequence inline."""
        if isinstance(value, (list, tuple)):
            return "[" + ", ".join(
                self._encode_scalar(item) for item in value
            ) + "]"
        if isinstance(value, Mapping):
            if not value:
                return "{}"
            raise TypeError("non-empty mappings must be rendered recursively")
        return self._encode_scalar(value)

    def _table_cell(self, value: Any) -> str:
        """Keep one encoded value inside its Markdown table column."""
        if _is_scalar(value) or self._can_inline_sequence(value):
            encoded = self._inline_value(value)
        else:
            bounded = self._bounded_json(value)
            encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
        return encoded.replace("|", "\\|")


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


def _display_label(value: str, *, capitalize: bool) -> str:
    """Turn a trusted field identifier into a short human-readable label."""
    safe = _escape_label(str(value)).replace("_", " ")
    if capitalize and safe:
        return safe[0].upper() + safe[1:]
    return safe


def _code_label(value: str) -> str:
    """Wrap a record handle in a fence that embedded backticks cannot close."""
    safe = _escape_text(str(value))
    longest_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", safe)),
        default=0,
    )
    fence = "`" * max(1, longest_run + 1)
    padding = " " if safe.startswith("`") or safe.endswith("`") else ""
    return f"{fence}{padding}{safe}{padding}{fence}"


def _clean_scalar(value: str) -> str:
    """Replace unsupported controls before JSON handles visible escaping."""
    return _CONTROL_CHARACTER.sub("\ufffd", value)


def _is_scalar(value: Any) -> bool:
    """Identify values that can be rendered without recursive structure."""
    return not isinstance(value, (Mapping, list, tuple))


def _preview_lengths(max_value_chars: int) -> tuple[int, int]:
    """Allocate most of a clipped preview to its diagnostically useful head."""
    head_chars = max(12, int(max_value_chars * 0.8))
    tail_chars = max(6, max_value_chars - head_chars)
    return head_chars, tail_chars


def _clip_message(text: str, max_chars: int) -> str:
    """Clip oversized structural text with explicit original-size metadata."""
    if len(text) <= max_chars:
        return text
    if max_chars < 48:
        return text[:max_chars]
    marker = f"Context clipped from {len(text)} characters.\n"
    available = max_chars - len(marker) - len("\n...\n")
    head_chars = max(1, available // 2)
    tail_chars = max(1, available - head_chars)
    return (
        marker
        + text[:head_chars]
        + "\n...\n"
        + text[-tail_chars:]
    )[:max_chars]
