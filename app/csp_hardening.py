"""CSP Level 3 hashes for audited legacy style and event attributes."""

from __future__ import annotations

import base64
import hashlib
import html as html_module
import logging
import re

logger = logging.getLogger(__name__)

_EVENT_ATTRIBUTE = re.compile(
    r"\s(?:onclick|onchange|oninput|onsubmit|onload|onerror|onkeyup|onkeydown|onblur|onfocus)"
    r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)')",
    re.IGNORECASE,
)
_STYLE_ATTRIBUTE = re.compile(
    r"\sstyle\s*=\s*(?:\"([^\"]*)\"|'([^']*)')",
    re.IGNORECASE,
)

# The pinned Iconify component creates one of these exact style attributes on
# its generated SVG.  They are fixed library constants, not user input.
_STATIC_STYLE_ATTRIBUTES = (
    "width: inherit;",
    "height: inherit;",
    "width: inherit;height: inherit;",
)


def _hash_source(value: str) -> str:
    normalized = html_module.unescape(value)
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def attribute_hash_sources(markup: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    values = {
        match.group(1) if match.group(1) is not None else match.group(2)
        for match in pattern.finditer(markup)
    }
    return tuple(sorted(_hash_source(value) for value in values))


def rendered_attribute_hashes(markup: str) -> dict[str, tuple[str, ...]]:
    style_hashes = set(attribute_hash_sources(markup, _STYLE_ATTRIBUTE))
    style_hashes.update(_hash_source(value) for value in _STATIC_STYLE_ATTRIBUTES)
    return {
        "script-src-attr": attribute_hash_sources(markup, _EVENT_ATTRIBUTE),
        "style-src-attr": tuple(sorted(style_hashes)),
    }


def _replace_directive(header: str, name: str, hashes: tuple[str, ...]) -> str:
    sources = "'unsafe-hashes'"
    if hashes:
        sources += " " + " ".join(hashes)
    replacement = f"{name} {sources}"
    pattern = re.compile(rf"(?:^|(?<=;))\s*{re.escape(name)}\s+[^;]*")
    if pattern.search(header):
        return pattern.sub(" " + replacement, header, count=1)
    return header.rstrip("; ") + "; " + replacement


def finalize_html_csp(response):
    """Replace scoped compatibility directives with exact rendered hashes."""
    header = response.headers.get("Content-Security-Policy")
    if not header or response.direct_passthrough or response.mimetype != "text/html":
        return response
    try:
        markup = response.get_data(as_text=True)
        hashes = rendered_attribute_hashes(markup)
        maximum = 128
        for directive, sources in hashes.items():
            if len(sources) > maximum:
                logger.error(
                    "CSP attribute hash budget exceeded directive=%s unique=%d maximum=%d",
                    directive, len(sources), maximum,
                )
                sources = ()
            header = _replace_directive(header, directive, sources)
        response.headers["Content-Security-Policy"] = header
    except Exception:
        # Static policy has no broad inline permission. Enrichment failure is
        # fail-closed: affected legacy attributes stay blocked.
        logger.exception("CSP attribute hash enrichment failed")
    return response
