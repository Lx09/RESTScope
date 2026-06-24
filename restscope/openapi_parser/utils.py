"""Utility functions for the OpenAPI parser."""


def ensure_dict(value, default=None):
    """
    Ensure a value is a dictionary.

    Args:
        value: The value to check.
        default: The default value if not a dict.

    Returns:
        The value if it's a dict, otherwise the default.
    """
    if default is None:
        default = {}
    return value if isinstance(value, dict) else default


def ensure_list(value, default=None):
    """
    Ensure a value is a list.

    Args:
        value: The value to check.
        default: The default value if not a list.

    Returns:
        The value if it's a list, otherwise the default.
    """
    if default is None:
        default = []
    return value if isinstance(value, list) else default


def safe_get(d, key, default=None):
    """
    Safely get a value from a dictionary.

    Args:
        d: The dictionary.
        key: The key to get.
        default: The default value if key doesn't exist.

    Returns:
        The value or default.
    """
    if not isinstance(d, dict):
        return default
    return d.get(key, default)


def is_ref_object(obj):
    """
    Check if an object is a $ref reference.

    Args:
        obj: The object to check.

    Returns:
        True if it's a $ref object.
    """
    if not isinstance(obj, dict):
        return False
    return "$ref" in obj and len(obj) == 1


def normalize_media_type(media_type: str) -> str:
    """
    Normalize a media type string.

    Args:
        media_type: The media type string.

    Returns:
        Normalized media type (lowercase).
    """
    return media_type.lower().strip()
