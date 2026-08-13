"""Reference resolver for $ref resolution."""

import os
import urllib.parse

from .exceptions import RecursiveReferenceError, ReferenceResolutionError


class ReferenceResolver:
    """
    Resolves JSON Pointer references ($ref) in OpenAPI specs.

    Supports:
    - Local JSON Pointer (e.g., #/components/schemas/User)
    - Relative file paths (e.g., common.yaml#/User)
    - file:// URIs
    - http:// and https:// URIs
    """

    def __init__(self, root_location: str | None, root_document: dict):
        """
        Initialize the resolver.

        Args:
            root_location: The location of the root document (file path or URL).
            root_document: The root document dictionary.
        """
        self._root_location: str | None = root_location
        self._root_document: dict = root_document
        self._scope_stack: list[str] = []
        self._document_cache: dict[str, dict] = {}
        self._resolved_ref_cache: dict[tuple[str, str], tuple[str, object]] = {}
        self._resolving_stack: list[tuple[str, str]] = []

    def push_scope(self, scope: str) -> None:
        """Push a new scope onto the stack."""
        self._scope_stack.append(scope)

    def pop_scope(self) -> None:
        """Pop the current scope from the stack."""
        if self._scope_stack:
            self._scope_stack.pop()

    @property
    def resolution_scope(self) -> str:
        """Get the current resolution scope."""
        if self._scope_stack:
            return self._scope_stack[-1]
        return self._root_location or ""

    def _resolve_absolute_ref(
        self, current_scope: str, ref: str
    ) -> tuple[str, str]:
        """
        Resolve a reference to an absolute document URI and JSON Pointer.

        Args:
            current_scope: The current resolution scope.
            ref: The reference string.

        Returns:
            A tuple of (document_uri, json_pointer).
        """
        # Parse the ref
        if "#" in ref:
            doc_part, pointer = ref.split("#", 1)
        else:
            doc_part = ref
            pointer = ""

        # Handle empty doc_part (local pointer)
        if not doc_part:
            doc_uri = current_scope
        elif doc_part.startswith(("http://", "https://", "file://")):
            doc_uri = doc_part
        else:
            # Relative path
            if current_scope:
                if current_scope.startswith(("http://", "https://")):
                    # Relative URL
                    doc_uri = urllib.parse.urljoin(current_scope, doc_part)
                elif current_scope.startswith("file://"):
                    # Relative file path
                    base_path = current_scope[7:]  # Remove file://
                    base_dir = os.path.dirname(base_path)
                    abs_path = os.path.normpath(os.path.join(base_dir, doc_part))
                    doc_uri = f"file://{abs_path}"
                else:
                    # Local file path
                    base_dir = os.path.dirname(current_scope)
                    abs_path = os.path.normpath(os.path.join(base_dir, doc_part))
                    doc_uri = abs_path
            else:
                doc_uri = doc_part

        return doc_uri, pointer

    def _load_document(self, doc_uri: str) -> dict:
        """
        Load a document from a URI.

        Args:
            doc_uri: The document URI.

        Returns:
            The document dictionary.
        """
        # Check cache
        if doc_uri in self._document_cache:
            return self._document_cache[doc_uri]

        # Root document
        if doc_uri == self._root_location or (
            not doc_uri and self._root_location is None
        ):
            return self._root_document

        # Handle file:// URIs
        if doc_uri.startswith("file://"):
            file_path = doc_uri[7:]
            try:
                import yaml

                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    doc = yaml.safe_load(content)
                    self._document_cache[doc_uri] = doc
                    return doc
            except Exception as e:  # noqa: BLE001
                raise ReferenceResolutionError(f"Failed to load file {file_path}: {e}")

        # Handle http:// and https:// URIs
        if doc_uri.startswith(("http://", "https://")):
            try:
                import urllib.request

                import yaml

                with urllib.request.urlopen(doc_uri, timeout=30) as response:
                    content = response.read().decode("utf-8")
                    doc = yaml.safe_load(content)
                    self._document_cache[doc_uri] = doc
                    return doc
            except Exception as e:  # noqa: BLE001
                raise ReferenceResolutionError(f"Failed to load URL {doc_uri}: {e}")

        # Handle local file paths
        if os.path.exists(doc_uri):
            try:
                import yaml

                with open(doc_uri, "r", encoding="utf-8") as f:
                    content = f.read()
                    doc = yaml.safe_load(content)
                    self._document_cache[doc_uri] = doc
                    return doc
            except Exception as e:  # noqa: BLE001
                raise ReferenceResolutionError(f"Failed to load file {doc_uri}: {e}")

        raise ReferenceResolutionError(f"Cannot load document from {doc_uri}")

    def _resolve_json_pointer(self, document: dict, pointer: str) -> object:
        """
        Resolve a JSON Pointer within a document.

        Args:
            document: The document dictionary.
            pointer: The JSON Pointer (e.g., /components/schemas/User).

        Returns:
            The resolved node.
        """
        if not pointer:
            return document

        # Remove leading #
        pointer = pointer.removeprefix("#")

        # Remove leading /
        if not pointer.startswith("/"):
            raise ReferenceResolutionError(f"Invalid JSON Pointer: {pointer}")

        # Split into segments
        segments = pointer[1:].split("/")

        # Decode JSON Pointer escapes
        def decode_segment(seg: str) -> str:
            return seg.replace("~1", "/").replace("~0", "~")

        segments = [decode_segment(seg) for seg in segments]

        # Navigate through the document
        current = document
        for i, segment in enumerate(segments):
            if isinstance(current, dict):
                if segment not in current:
                    raise ReferenceResolutionError(
                        f"JSON Pointer segment '{segment}' not found at position {i}"
                    )
                current = current[segment]
            elif isinstance(current, list):
                try:
                    index = int(segment)
                    current = current[index]
                except (ValueError, IndexError):
                    raise ReferenceResolutionError(
                        f"Invalid array index '{segment}' in JSON Pointer"
                    )
            else:
                raise ReferenceResolutionError(
                    f"Cannot navigate through non-container node at segment '{segment}'"
                )

        return current

    def resolve(self, ref: str) -> tuple[str, object]:
        """
        Resolve a reference.

        Args:
            ref: The reference string (e.g., #/components/schemas/User).

        Returns:
            A tuple of (new_scope, resolved_node).

        Raises:
            RecursiveReferenceError: If a recursive reference is detected.
            ReferenceResolutionError: If the reference cannot be resolved.
        """
        cache_key = (self.resolution_scope, ref)
        if cache_key in self._resolved_ref_cache:
            return self._resolved_ref_cache[cache_key]

        doc_uri, pointer = self._resolve_absolute_ref(
            self.resolution_scope, ref
        )
        ref_identity = (doc_uri, pointer)

        if ref_identity in self._resolving_stack:
            raise RecursiveReferenceError(
                f"Recursive reference detected: {doc_uri}{pointer}"
            )

        self._resolving_stack.append(ref_identity)
        try:
            document = self._load_document(doc_uri)
            node = self._resolve_json_pointer(document, pointer)
            result = (doc_uri, node)
            self._resolved_ref_cache[cache_key] = result
            return result
        except (RecursiveReferenceError, ReferenceResolutionError):
            raise
        except Exception as exc:
            raise ReferenceResolutionError(str(exc)) from exc
        finally:
            self._resolving_stack.pop()
