"""Small allow-list HTML sanitizer for tenant-authored rich text.

The project editor stores a limited subset of formatting HTML.  We avoid
rendering arbitrary user HTML by allowing only semantic text tags and safe
HTTP(S)/mailto links.  Scripts, styles, embeds, event handlers, and unknown
attributes are discarded.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from markupsafe import Markup

_ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li",
    "h2", "h3", "h4", "blockquote", "code", "pre", "a",
}
_VOID_TAGS = {"br"}
_ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}


def _safe_href(value: str) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in _ALLOWED_LINK_SCHEMES:
        return None
    return candidate


class _RichTextSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "svg", "math"}:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in _ALLOWED_TAGS:
            return
        if tag == "a":
            attrs_map = {str(k).lower(): str(v or "") for k, v in attrs}
            href = _safe_href(attrs_map.get("href", ""))
            if href:
                self.parts.append(
                    f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">'
                )
                self.open_tags.append(tag)
            return
        self.parts.append(f"<{tag}>")
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "svg", "math"}:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag in self.open_tags:
            while self.open_tags:
                current = self.open_tags.pop()
                self.parts.append(f"</{current}>")
                if current == tag:
                    break

    def handle_data(self, data: str):
        if not self.skip_depth:
            self.parts.append(escape(data))

    def handle_entityref(self, name: str):
        if not self.skip_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str):
        if not self.skip_depth:
            self.parts.append(f"&#{name};")

    def close(self):
        super().close()
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")


def sanitize_rich_text(value: str | None) -> str:
    """Return safe, limited HTML suitable for storage or template output."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "<" not in raw and ">" not in raw:
        # Preserve paragraphs for plain-text users without trusting markup.
        paragraphs = [part.strip() for part in raw.replace("\r\n", "\n").split("\n\n") if part.strip()]
        return "".join(f"<p>{escape(part).replace(chr(10), '<br>')}</p>" for part in paragraphs)
    parser = _RichTextSanitizer()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts).strip()


def richtext_markup(value: str | None) -> Markup:
    """Jinja-facing wrapper that sanitizes before marking output safe."""
    return Markup(sanitize_rich_text(value))
