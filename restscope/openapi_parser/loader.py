"""Loader module for loading OpenAPI specs from various sources."""

import json
import os
from pathlib import Path

import yaml

from .constants import SOURCE_KIND_FILE, SOURCE_KIND_MEMORY, SOURCE_KIND_URL
from .exceptions import LoaderError
from .ir import ParseInput


def _is_url(text: str) -> bool:
    """Check if a string is a URL."""
    return text.startswith(("http://", "https://"))


def _is_local_path(text: str) -> bool:
    """Check if a string is an existing local file path."""
    return os.path.exists(text)


def _parse_yaml_or_json(content: str) -> dict[str, object]:
    """Parse content as YAML or JSON."""
    # Try JSON first
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try YAML
    try:
        result = yaml.safe_load(content)
        if isinstance(result, dict):
            return result
        elif result is None:
            return {}
    except yaml.YAMLError as e:
        raise LoaderError(f"Failed to parse content as YAML or JSON: {e}")

    raise LoaderError("Content did not parse to a valid YAML/JSON object")


def _load_from_url(url: str) -> dict[str, object]:
    """Load spec from a URL."""
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=30) as response:
            content = response.read().decode("utf-8")
            return _parse_yaml_or_json(content)
    except Exception as e:  # noqa: BLE001
        raise LoaderError(f"Failed to load from URL {url}: {e}")


def _load_from_file(file_path: str) -> dict[str, object]:
    """Load spec from a local file."""
    try:
        abs_path = os.path.abspath(file_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        return _parse_yaml_or_json(content)
    except Exception as e:  # noqa: BLE001
        raise LoaderError(f"Failed to load from file {file_path}: {e}")


def load_parse_input(source: object) -> ParseInput:
    """
    Load and parse the input source into a ParseInput object.

    Supports:
    - dict: used directly as raw_document
    - str: URL, local file path, or YAML/JSON content
    - pathlib.Path: local file path

    Returns:
        ParseInput with raw_document, source_location, and source_kind
    """
    # Handle dict input
    if isinstance(source, dict):
        return ParseInput(
            raw_document=source,
            source_location=None,
            source_kind=SOURCE_KIND_MEMORY,
        )

    # Handle Path object
    if isinstance(source, Path):
        abs_path = str(source.resolve())
        content_dict = _load_from_file(abs_path)
        return ParseInput(
            raw_document=content_dict,
            source_location=abs_path,
            source_kind=SOURCE_KIND_FILE,
        )

    # Handle string input
    if isinstance(source, str):
        # Check if it's a URL
        if _is_url(source):
            content_dict = _load_from_url(source)
            return ParseInput(
                raw_document=content_dict,
                source_location=source,
                source_kind=SOURCE_KIND_URL,
            )

        # Check if it's an existing local file path
        if _is_local_path(source):
            abs_path = os.path.abspath(source)
            content_dict = _load_from_file(abs_path)
            return ParseInput(
                raw_document=content_dict,
                source_location=abs_path,
                source_kind=SOURCE_KIND_FILE,
            )

        # Otherwise, treat as YAML/JSON content
        content_dict = _parse_yaml_or_json(source)
        return ParseInput(
            raw_document=content_dict,
            source_location=None,
            source_kind=SOURCE_KIND_MEMORY,
        )

    raise LoaderError(f"Unsupported input type: {type(source)}")
