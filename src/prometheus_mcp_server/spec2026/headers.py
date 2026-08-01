#!/usr/bin/env python

"""HTTP header validation and ``x-mcp-header`` support for MCP 2026-07-28 (F6).

This module implements three independent concerns of the 2026-07-28 header
extension, all with standard library only:

A. The base64 sentinel codec (``=?base64?{b64}?=``) used to transport header
   values that are not safe plain ASCII.
B. Validation of the ``Mcp-Method`` / ``Mcp-Name`` / ``MCP-Protocol-Version`` /
   ``Mcp-Param-{Name}`` headers against the JSON-RPC request body, producing
   ``HeaderMismatch`` (-32020) errors.
C. Validation of the ``x-mcp-header`` annotation inside a tool ``inputSchema``,
   plus extraction of the annotated values into outgoing headers.

Strict enforcement is **opt-in**: :func:`validate_request_headers` is a no-op
unless ``strict=True`` is passed, so 2025-11-25 clients that send no ``Mcp-*``
headers at all keep working unchanged.

The ``Mcp-*`` headers are *optional* routing hints. A client is never obliged to
send one, so an absent header is never an error even when the body carries the
corresponding value; ``HeaderMismatch`` is reserved for a header that actually
disagrees with the body, or for one sent when the body has nothing to mirror.

``MCP-Protocol-Version`` is deliberately treated differently from the ``Mcp-*``
hints: it is a Streamable-HTTP *transport* header that the MCP specification has
REQUIRED on every post-initialize HTTP request since 2025-06-18, not a mirror of
a body field. See :func:`validate_request_headers` for the exact rule.
"""

import base64
import binascii
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from prometheus_mcp_server.logging_config import get_logger

logger = get_logger()

# The error code lives in the shared constants module, but this module must stay
# importable on its own (and must not crash if constants.py is absent or a later
# revision drops the symbol), so the literal is kept as a defensive fallback.
try:  # pragma: no cover - exercised implicitly by whichever branch is installed
    from prometheus_mcp_server.spec2026.constants import HEADER_MISMATCH
except Exception:  # pragma: no cover - defensive: constants module not present yet
    HEADER_MISMATCH = -32020

try:  # pragma: no cover - exercised implicitly by whichever branch is installed
    from prometheus_mcp_server.spec2026.constants import META_PROTOCOL_VERSION
except Exception:  # pragma: no cover - defensive: constants module not present yet
    META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"

try:  # pragma: no cover - exercised implicitly by whichever branch is installed
    from prometheus_mcp_server.spec2026.constants import PROTOCOL_VERSION_2026
except Exception:  # pragma: no cover - defensive: constants module not present yet
    PROTOCOL_VERSION_2026 = "2026-07-28"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Sentinel markers are lowercase and compared case-sensitively.
SENTINEL_PREFIX = "=?base64?"
SENTINEL_SUFFIX = "?="

#: Header carrying the JSON-RPC ``method`` of the body.
HEADER_MCP_METHOD = "Mcp-Method"
#: Header carrying ``params.name`` (or ``params.uri``) of the body.
HEADER_MCP_NAME = "Mcp-Name"
#: Header carrying the negotiated protocol version.
HEADER_MCP_PROTOCOL_VERSION = "MCP-Protocol-Version"
#: Prefix for headers generated from ``x-mcp-header`` annotations.
PARAM_HEADER_PREFIX = "Mcp-Param-"

#: JSON Schema keyword carrying the annotation.
ANNOTATION_KEY = "x-mcp-header"

#: Only these primitive types may be annotated. ``number`` is explicitly NOT
#: permitted by the specification (float formatting is not interoperable).
ALLOWED_ANNOTATION_TYPES = ("integer", "string", "boolean")

#: RFC 9110 ``token`` = 1*tchar.
_TCHAR = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]"
_TOKEN_RE = re.compile(r"\A" + _TCHAR + r"+\Z")

#: Safe plain ASCII: tab, space, and the printable range 0x21-0x7E.
_SAFE_CHARS = frozenset({"\t", " "} | {chr(code) for code in range(0x21, 0x7F)})

#: The JSON number grammar (RFC 8259 section 6), anchored. Used instead of
#: ``float()`` so header values that are not legal JSON numbers never compare
#: equal to a numeric body value.
_JSON_NUMBER_RE = re.compile(r"\A-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?\Z")


# ---------------------------------------------------------------------------
# (A) Base64 sentinel codec
# ---------------------------------------------------------------------------

def looks_like_sentinel(value: str) -> bool:
    """Report whether a string is shaped like an encoded sentinel value.

    The markers are case-sensitive and must not overlap, so the value has to be
    at least ``len(prefix) + len(suffix)`` characters long.

    Args:
        value: Raw header value to inspect.

    Returns:
        True when the value starts with ``=?base64?`` and ends with ``?=``
        without the two markers overlapping.
    """
    if not isinstance(value, str):
        return False
    if len(value) < len(SENTINEL_PREFIX) + len(SENTINEL_SUFFIX):
        return False
    return value.startswith(SENTINEL_PREFIX) and value.endswith(SENTINEL_SUFFIX)


def is_safe_plain_ascii(value: str) -> bool:
    """Report whether a value may travel unencoded in an HTTP header.

    A value is safe when every character is a tab, a space, or printable ASCII
    (0x21-0x7E), it has no leading or trailing whitespace, and it is not itself
    shaped like an encoded sentinel value (which would be ambiguous on decode).

    Args:
        value: Value to inspect.

    Returns:
        True when the value can be sent verbatim.
    """
    if not isinstance(value, str):
        return False
    if any(char not in _SAFE_CHARS for char in value):
        return False
    if value != value.strip():
        return False
    return not looks_like_sentinel(value)


def encode_header_value(value: str) -> str:
    """Encode a value for transport, applying the base64 sentinel when needed.

    Args:
        value: Value to place in an HTTP header.

    Returns:
        The value verbatim when it is safe plain ASCII, otherwise
        ``=?base64?{Base64EncodedValue}?=``.
    """
    if is_safe_plain_ascii(value):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"{SENTINEL_PREFIX}{encoded}{SENTINEL_SUFFIX}"


def decode_header_value(value: str) -> str:
    """Decode a header value, unwrapping the base64 sentinel when present.

    Decoding is lenient: a malformed sentinel payload is logged and returned
    verbatim so a bad header degrades into an ordinary comparison mismatch
    rather than raising out of the request path.

    Args:
        value: Raw header value as received on the wire.

    Returns:
        The decoded value, or the input unchanged when it is not an encoded
        sentinel (or the payload cannot be decoded).
    """
    if not looks_like_sentinel(value):
        return value

    payload = value[len(SENTINEL_PREFIX):-len(SENTINEL_SUFFIX)]
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        logger.warning(
            "Malformed base64 sentinel in header value; using raw value",
            error=str(e),
            error_type=type(e).__name__,
        )
        return value


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeaderAnnotation:
    """A validated ``x-mcp-header`` annotation found on an input-schema property.

    Attributes:
        token: The annotation value, i.e. the ``{Name}`` of ``Mcp-Param-{Name}``.
        path: Property path from the schema root, e.g. ``("filters", "region")``.
        type_name: Declared JSON Schema type of the annotated property.
    """

    token: str
    path: Tuple[str, ...]
    type_name: str

    @property
    def header_name(self) -> str:
        """Full HTTP header name for this annotation."""
        return f"{PARAM_HEADER_PREFIX}{self.token}"


@dataclass(frozen=True)
class SchemaAnnotationError:
    """A problem found while validating ``x-mcp-header`` annotations.

    Attributes:
        code: Short machine-readable reason, e.g. ``"invalid_token"``.
        message: Human-readable explanation.
        path: Property path of the offending node (empty tuple for the root).
        token: The offending annotation value, when it is a string.
    """

    code: str
    message: str
    path: Tuple[str, ...] = ()
    token: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Render the error as a JSON-serialisable dictionary."""
        return {
            "code": self.code,
            "message": self.message,
            "path": list(self.path),
            "token": self.token,
        }


@dataclass(frozen=True)
class HeaderValidationError:
    """A single ``HeaderMismatch`` finding.

    Attributes:
        header: The HTTP header name involved.
        message: Human-readable explanation.
        reason: Short machine-readable reason.
        header_value: Decoded header value, when one was supplied.
        body_value: The value found in the request body, when one was present.
    """

    header: str
    message: str
    reason: str
    header_value: Optional[str] = None
    body_value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        """Render the error as a JSON-serialisable dictionary."""
        return {
            "header": self.header,
            "message": self.message,
            "reason": self.reason,
            "headerValue": self.header_value,
            "bodyValue": self.body_value,
        }


# ---------------------------------------------------------------------------
# (C) x-mcp-header inputSchema validation
# ---------------------------------------------------------------------------

def _iter_reachable_properties(
    schema: Any,
    prefix: Tuple[str, ...] = (),
    seen: Optional[set] = None,
):
    """Yield ``(path, subschema)`` for properties reachable via ``properties`` chains.

    Descent happens through the ``properties`` keyword only. ``items``,
    ``oneOf``/``anyOf``/``allOf``/``not``, ``if``/``then``/``else`` and ``$ref``
    are deliberately never followed, so anything nested under them is not
    statically reachable.
    """
    if seen is None:
        seen = set()
    if not isinstance(schema, dict) or id(schema) in seen:
        return
    seen.add(id(schema))

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return

    for name, subschema in properties.items():
        if not isinstance(name, str) or not isinstance(subschema, dict):
            continue
        path = prefix + (name,)
        yield path, subschema
        yield from _iter_reachable_properties(subschema, path, seen)


def _iter_all_dicts(node: Any, seen: Optional[set] = None):
    """Yield every dictionary anywhere in a JSON-Schema-shaped structure."""
    if seen is None:
        seen = set()
    if isinstance(node, dict):
        if id(node) in seen:
            return
        seen.add(id(node))
        yield node
        for value in node.values():
            yield from _iter_all_dicts(value, seen)
    elif isinstance(node, list):
        if id(node) in seen:
            return
        seen.add(id(node))
        for item in node:
            yield from _iter_all_dicts(item, seen)


def _validate_token(token: Any, path: Tuple[str, ...]) -> Optional[SchemaAnnotationError]:
    """Check one annotation value against the syntactic constraints."""
    if not isinstance(token, str):
        return SchemaAnnotationError(
            code="not_a_string",
            message=f"{ANNOTATION_KEY} must be a string, got {type(token).__name__}",
            path=path,
        )
    if token == "":
        return SchemaAnnotationError(
            code="empty",
            message=f"{ANNOTATION_KEY} must not be empty",
            path=path,
            token=token,
        )
    if "\r" in token or "\n" in token:
        return SchemaAnnotationError(
            code="crlf",
            message=f"{ANNOTATION_KEY} must not contain CR or LF",
            path=path,
            token=token,
        )
    if not _TOKEN_RE.match(token):
        return SchemaAnnotationError(
            code="invalid_token",
            message=f"{ANNOTATION_KEY} must be an RFC 9110 tchar token",
            path=path,
            token=token,
        )
    return None


def _collect_annotations(
    input_schema: Any,
) -> Tuple[List[HeaderAnnotation], List[SchemaAnnotationError]]:
    """Collect valid annotations and every constraint violation in one pass."""
    annotations: List[HeaderAnnotation] = []
    errors: List[SchemaAnnotationError] = []

    if not isinstance(input_schema, dict):
        return annotations, errors

    reachable: Dict[int, Tuple[str, ...]] = {}
    ordered: List[Tuple[Tuple[str, ...], Dict[str, Any]]] = []
    for path, subschema in _iter_reachable_properties(input_schema):
        reachable[id(subschema)] = path
        ordered.append((path, subschema))

    seen_tokens: Dict[str, Tuple[str, ...]] = {}

    for path, subschema in ordered:
        if ANNOTATION_KEY not in subschema:
            continue
        token = subschema[ANNOTATION_KEY]

        token_error = _validate_token(token, path)
        if token_error is not None:
            errors.append(token_error)
            continue

        type_name = subschema.get("type")
        if type_name not in ALLOWED_ANNOTATION_TYPES:
            errors.append(SchemaAnnotationError(
                code="invalid_type",
                message=(
                    f"{ANNOTATION_KEY} requires a primitive type "
                    f"({', '.join(ALLOWED_ANNOTATION_TYPES)}), got {type_name!r}"
                ),
                path=path,
                token=token,
            ))
            continue

        lowered = token.lower()
        if lowered in seen_tokens:
            errors.append(SchemaAnnotationError(
                code="duplicate",
                message=(
                    f"{ANNOTATION_KEY} {token!r} duplicates the annotation at "
                    f"{'.'.join(seen_tokens[lowered]) or '<root>'} "
                    "(names are compared case-insensitively)"
                ),
                path=path,
                token=token,
            ))
            continue
        seen_tokens[lowered] = path

        annotations.append(HeaderAnnotation(token=token, path=path, type_name=type_name))

    # Anything carrying the annotation that is not a statically reachable
    # property is rejected: nested in items/combinators/conditionals/$defs, or
    # sitting on the schema root (which is not a property at all).
    for node in _iter_all_dicts(input_schema):
        if ANNOTATION_KEY not in node or id(node) in reachable:
            continue
        token = node[ANNOTATION_KEY]
        if node is input_schema:
            errors.append(SchemaAnnotationError(
                code="not_a_property",
                message=f"{ANNOTATION_KEY} may only annotate a property, not the schema root",
                path=(),
                token=token if isinstance(token, str) else None,
            ))
        else:
            errors.append(SchemaAnnotationError(
                code="unreachable",
                message=(
                    f"{ANNOTATION_KEY} is not statically reachable from the schema "
                    "root through 'properties' chains"
                ),
                path=(),
                token=token if isinstance(token, str) else None,
            ))

    return annotations, errors


def validate_header_annotations(input_schema: Any) -> List[SchemaAnnotationError]:
    """Validate every ``x-mcp-header`` annotation in a tool input schema.

    Enforced constraints: the annotation value must be a non-empty RFC 9110
    ``tchar`` token containing no CR/LF, must be unique within the schema when
    compared case-insensitively, must sit on a property whose type is one of
    ``integer``/``string``/``boolean`` (``number`` is not permitted), and must be
    statically reachable from the schema root through ``properties`` chains only.

    Args:
        input_schema: The tool's JSON Schema ``inputSchema``.

    Returns:
        A list of :class:`SchemaAnnotationError`; empty when the schema is valid.
    """
    return _collect_annotations(input_schema)[1]


def collect_header_annotations(input_schema: Any) -> List[HeaderAnnotation]:
    """Collect the annotations of a tool input schema that pass validation.

    Args:
        input_schema: The tool's JSON Schema ``inputSchema``.

    Returns:
        A list of valid :class:`HeaderAnnotation`, in schema order. Invalid
        annotations are omitted; use :func:`validate_header_annotations` to see
        why.
    """
    return _collect_annotations(input_schema)[0]


# ---------------------------------------------------------------------------
# Value formatting and comparison
# ---------------------------------------------------------------------------

_MISSING = object()


def _lookup_path(container: Any, path: Sequence[str]) -> Any:
    """Return the value at an exact property path, or ``_MISSING``."""
    current = container
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _format_header_value(value: Any) -> str:
    """Render a primitive body value the way it appears in a header."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_number(value: Any) -> Optional[float]:
    """Coerce a value to a float for numeric comparison, or return None.

    Strings are accepted only when they match the JSON number grammar. Python's
    own ``float()`` is far more permissive -- it accepts PEP 515 underscore
    separators (``"4_2"``), Unicode decimal digits (``"４２"``), ``"inf"`` and
    ``"nan"`` -- none of which a client could legitimately have produced from a
    JSON number, so accepting them would let a header that does not encode the
    body value pass validation.

    Args:
        value: Header string or body value to coerce.

    Returns:
        The numeric value, or None when it is not a number.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if not _JSON_NUMBER_RE.match(value):
            return None
        try:
            return float(value)
        except ValueError:  # pragma: no cover - the grammar already guarantees this parses
            return None
    return None


def _normalize_uri(value: Any) -> Any:
    """Normalise a URI so equivalent spellings compare equal.

    A client that mirrors ``params.uri`` into ``Mcp-Name`` sends the bytes it put
    in the body, but any intermediary that round-trips the URI through a parser
    may hand back a normalised form (lowercased scheme and host, an explicit
    ``/`` path for a bare authority). Both sides are normalised the same way so
    such a round trip is not reported as a mismatch.

    Args:
        value: Candidate URI. Non-strings are returned unchanged.

    Returns:
        The normalised URI, or the input unchanged when it cannot be parsed.
    """
    if not isinstance(value, str):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme:
        return value
    path = parts.path
    if parts.netloc and not path:
        path = "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment))


def _values_match(header_value: str, body_value: Any, type_name: Optional[str] = None) -> bool:
    """Compare a decoded header value against a body value.

    Header values are case-sensitive. Integer parameters compare numerically, so
    a header of ``"42.0"`` matches a body value of ``42``. ``uri`` is a
    pseudo-type used internally for the ``Mcp-Name``/``params.uri`` pairing and
    compares after URI normalisation.

    Args:
        header_value: Decoded header value.
        body_value: Value taken from the request body.
        type_name: Declared JSON Schema type of the parameter, when known.

    Returns:
        True when the two represent the same value.
    """
    if isinstance(body_value, bool):
        return header_value == _format_header_value(body_value)

    if type_name == "uri":
        return _normalize_uri(header_value) == _normalize_uri(body_value)

    # "number" is not a permitted annotation type, so an integer annotation is
    # the only declared type that reaches the numeric branch; a numeric body
    # value gets there on its own regardless of what was declared.
    if type_name == "integer" or isinstance(body_value, (int, float)):
        left = _parse_number(header_value)
        right = _parse_number(body_value)
        if left is not None and right is not None:
            return left == right

    return header_value == _format_header_value(body_value)


# ---------------------------------------------------------------------------
# (C) Outgoing header extraction
# ---------------------------------------------------------------------------

def extract_param_headers(
    annotations: Sequence[HeaderAnnotation],
    arguments: Any,
) -> Dict[str, str]:
    """Build the ``Mcp-Param-*`` headers for a set of tool arguments.

    The value is read at the annotated property's exact path. When no value is
    present there -- the path is missing, or the value is null -- the header is
    omitted entirely, which is not an error.

    Args:
        annotations: Validated annotations, e.g. from
            :func:`collect_header_annotations`.
        arguments: The tool call arguments (``params.arguments``).

    Returns:
        Mapping of header name to encoded header value.
    """
    headers: Dict[str, str] = {}
    for annotation in annotations:
        value = _lookup_path(arguments, annotation.path)
        if value is _MISSING or value is None:
            continue
        headers[annotation.header_name] = encode_header_value(_format_header_value(value))
    return headers


# ---------------------------------------------------------------------------
# (B) Header / body validation
# ---------------------------------------------------------------------------

def strict_headers_enabled() -> bool:
    """Read the ``PROMETHEUS_MCP_STRICT_HEADERS`` opt-in switch.

    The environment is read at call time (not import time) so it can be changed
    per test. Callers that carry their own configuration should pass ``strict``
    explicitly instead of relying on this helper.

    Returns:
        True when strict header validation is switched on. Default is False.
    """
    return os.environ.get("PROMETHEUS_MCP_STRICT_HEADERS", "False").lower() in ("true", "1", "yes")


def _normalize_headers(headers: Optional[Mapping[str, str]]) -> Dict[str, str]:
    """Lowercase header names so lookups are case-insensitive."""
    if not headers:
        return {}
    return {str(name).lower(): value for name, value in headers.items()}


def _body_meta(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the request ``_meta`` mapping, preferring the one inside params."""
    params = body.get("params")
    if isinstance(params, Mapping):
        meta = params.get("_meta")
        if isinstance(meta, Mapping):
            return meta
    meta = body.get("_meta")
    return meta if isinstance(meta, Mapping) else {}


def _check_scalar(
    normalized: Mapping[str, str],
    header: str,
    body_value: Any,
    type_name: Optional[str] = None,
) -> Optional[HeaderValidationError]:
    """Compare one header against one body value, honouring the absence rules.

    The rule is asymmetric, which is the whole point: the ``Mcp-*`` headers are
    optional routing hints, so an absent header is never an error, while a
    header sent for a body value that does not exist cannot be a hint about
    anything and is reported as ``unexpected_header``.
    """
    raw = normalized.get(header.lower())
    present = raw is not None
    has_body_value = body_value is not _MISSING and body_value is not None

    if not has_body_value:
        # Absent or null in the body: the header must be omitted, and its
        # absence is explicitly not an error.
        if present:
            return HeaderValidationError(
                header=header,
                message=f"{header} was sent but the request body carries no corresponding value",
                reason="unexpected_header",
                header_value=decode_header_value(raw),
            )
        return None

    if not present:
        # A client is never obliged to send the hint. Treating this as a
        # mismatch would make every Mcp-* header mandatory and lock out every
        # client that predates 2026-07-28, including the initialize handshake.
        return None

    decoded = decode_header_value(raw)
    if not _values_match(decoded, body_value, type_name):
        return HeaderValidationError(
            header=header,
            message=f"{header} does not match the request body",
            reason="mismatch",
            header_value=decoded,
            body_value=body_value,
        )
    return None


def validate_request_headers(
    headers: Optional[Mapping[str, str]],
    body: Optional[Mapping[str, Any]],
    strict: bool = False,
    header_annotations: Optional[Sequence[HeaderAnnotation]] = None,
) -> List[HeaderValidationError]:
    """Validate the ``Mcp-*`` headers of a request against its JSON-RPC body.

    Checks ``Mcp-Method`` against ``method``, ``Mcp-Name`` against
    ``params.name`` (falling back to ``params.uri``), and one
    ``Mcp-Param-{Name}`` header per supplied annotation against the argument at
    that annotation's path.

    Header names are matched case-insensitively; header values are compared
    case-sensitively after base64-sentinel decoding. Integer parameters compare
    numerically, and a ``params.uri`` is compared after URI normalisation. Both
    absence rules are honoured: a header must be omitted when the corresponding
    body value is absent or null, and an omitted header is never an error
    because the ``Mcp-*`` headers are optional hints rather than requirements.

    ``MCP-Protocol-Version`` is only compared when the request body declares a
    protocol version *other than* 2026-07-28. It is a transport header the
    Streamable-HTTP spec requires on every post-initialize request, so it is
    routinely present with nothing in ``_meta`` to mirror (a 2025-11-25 client),
    and a 2026-07-28 client cannot put its own revision in it at all: the SDK
    transport rejects an unknown value with -32600 before the request is parsed,
    which forces the header to carry the transport-negotiated revision while
    ``_meta`` carries 2026-07-28. Comparing those two would reject every client
    that exists. Any other declared revision *is* one the transport can carry,
    so the comparison stays meaningful there.

    Args:
        headers: Incoming HTTP headers (any case-insensitive or plain mapping).
        body: The parsed JSON-RPC request body.
        strict: Opt-in switch. When False (the default) this function is a
            no-op, so 2025-11-25 clients that send no ``Mcp-*`` headers are
            unaffected.
        header_annotations: Validated ``x-mcp-header`` annotations for the tool
            being called, used to check the ``Mcp-Param-*`` headers.

    Returns:
        A list of :class:`HeaderValidationError`; empty when the request is
        consistent (or when ``strict`` is False).
    """
    if not strict:
        return []

    if not isinstance(body, Mapping):
        return []

    normalized = _normalize_headers(headers)
    errors: List[HeaderValidationError] = []

    method = body.get("method", _MISSING)
    error = _check_scalar(normalized, HEADER_MCP_METHOD, method)
    if error is not None:
        errors.append(error)

    params = body.get("params")
    params_mapping: Mapping[str, Any] = params if isinstance(params, Mapping) else {}

    name_value = params_mapping.get("name", _MISSING)
    name_type: Optional[str] = None
    if name_value is _MISSING or name_value is None:
        name_value = params_mapping.get("uri", _MISSING)
        name_type = "uri"
    error = _check_scalar(normalized, HEADER_MCP_NAME, name_value, type_name=name_type)
    if error is not None:
        errors.append(error)

    protocol_version = _body_meta(body).get(META_PROTOCOL_VERSION, _MISSING)
    if (
        protocol_version is not _MISSING
        and protocol_version is not None
        and protocol_version != PROTOCOL_VERSION_2026
    ):
        error = _check_scalar(normalized, HEADER_MCP_PROTOCOL_VERSION, protocol_version)
        if error is not None:
            errors.append(error)

    arguments = params_mapping.get("arguments")
    for annotation in header_annotations or ():
        value = _lookup_path(arguments, annotation.path)
        error = _check_scalar(
            normalized,
            annotation.header_name,
            value,
            type_name=annotation.type_name,
        )
        if error is not None:
            errors.append(error)

    if errors:
        logger.warning(
            "Header validation failed",
            method=method if method is not _MISSING else None,
            mismatch_count=len(errors),
            mismatched_headers=[error.header for error in errors],
        )

    return errors


def header_mismatch_error(errors: Sequence[HeaderValidationError]) -> Dict[str, Any]:
    """Render header validation findings as a JSON-RPC error object.

    Args:
        errors: Findings from :func:`validate_request_headers`.

    Returns:
        A JSON-RPC error dictionary with code ``-32020`` (HeaderMismatch) and a
        ``data.mismatches`` list describing each finding.
    """
    return {
        "code": HEADER_MISMATCH,
        "message": "HeaderMismatch: HTTP headers do not match the request body",
        "data": {"mismatches": [error.to_dict() for error in errors]},
    }
