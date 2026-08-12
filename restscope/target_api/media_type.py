"""Normalize HTTP media types shared across RESTScope domains.

OpenAPI parsing, request generation, target execution, response monitoring,
and presentation all compare Content-Type values through these two functions.
Keeping them here gives those independent callers one obvious owner.
"""


def normalize_media_type(media_type: str | None) -> str | None:
    """Return a lowercase type without parameters, or ``None`` when blank."""

    if media_type is None:
        return None
    normalized = media_type.split(";", 1)[0].strip().casefold()
    return normalized or None


def is_json_media_type(media_type: str | None) -> bool:
    """Return whether a media type denotes ordinary or vendor-specific JSON."""

    normalized = normalize_media_type(media_type)
    return normalized == "application/json" or bool(
        normalized and normalized.endswith("+json")
    )
