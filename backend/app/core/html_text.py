"""Converting an HTML email body to readable plain text.

Built on the standard library's `html.parser`. That is a deliberate choice over
BeautifulSoup or lxml: nothing in this project parses HTML today, and adding a
dependency to read what is mostly table-layout marketing markup would be a poor
trade. `HTMLParser` is lenient with malformed input by design — it never raises
on unclosed or mismatched tags, which is exactly the property real email needs.

What this is for: an ATS that sends HTML-only mail. Before this existed, such a
message fell through to Gmail's ~200-character `snippet`, so the classifier read
a preview instead of the email — and a preview routinely contains the polite
opening while the decision sits three paragraphs down.

Pure, deterministic, network-free.
"""

import re
from html.parser import HTMLParser

# Content that is never readable text. `title` is included because an email's
# <title> is boilerplate ("Message from ACME"), not part of what was written.
_SKIPPED_TAGS = frozenset({"script", "style", "head", "noscript", "title", "meta", "link"})

# Tags that end a line of prose.
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "div", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
        "section", "table", "tbody", "td", "th", "thead", "tr", "ul",
    }
)

# Inline CSS that hides an element. Preheader text — the invisible line an email
# client shows in the inbox preview — is the main offender, and it is usually a
# duplicate of the snippet we are trying to improve on. Detected by attribute
# rather than by resolving stylesheets: crude, but it catches the common case
# for the cost of one string check.
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|mso-hide\s*:\s*all|max-height\s*:\s*0", re.I)


class _TextExtractor(HTMLParser):
    """Accumulates readable text, skipping non-content and hidden elements."""

    def __init__(self) -> None:
        # convert_charrefs=True makes the parser decode entities itself, so
        # `&rsquo;` and `&nbsp;` arrive as characters in handle_data and no
        # separate unescape pass is needed (or wanted — a second pass could
        # decode text that was legitimately literal).
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        # A stack rather than a counter: malformed markup closes tags out of
        # order, and a stack can unwind to the right one instead of getting
        # stuck skipping the rest of the document.
        self._skipping: list[str] = []

    # --- structure ---------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._newline()
            return

        if self._is_hidden(tag, attrs):
            self._skipping.append(tag)
            return

        if self._skipping:
            return

        if tag == "li":
            self._parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if self._skipping:
            if tag in self._skipping:
                # Unwind to the matching tag, discarding any unclosed ones
                # opened inside it. Without this an unclosed <div> inside a
                # <style> block would swallow the rest of the email.
                while self._skipping and self._skipping.pop() != tag:
                    pass
            return

        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return
        # Whitespace inside text is collapsed here, at the point where HTML
        # semantics still apply: a newline in the source is formatting, not a
        # line break. Real emails hard-wrap their markup, so preserving those
        # newlines would chop every paragraph into fragments — and the only
        # line breaks that should survive are the ones this parser inserts for
        # block tags and <br>.
        self._parts.append(re.sub(r"\s+", " ", data))

    # --- helpers -----------------------------------------------------------

    def _is_hidden(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in _SKIPPED_TAGS:
            return True
        for name, value in attrs:
            if name == "hidden":
                return True
            if name == "style" and value and _HIDDEN_STYLE.search(value):
                return True
        return False

    def _newline(self) -> None:
        self._parts.append("\n")

    def text(self) -> str:
        return "".join(self._parts)


def _collapse(text: str) -> str:
    """Tidy extracted text without losing paragraph structure.

    Whitespace inside a line is collapsed (HTML source is full of indentation
    that means nothing), blank runs are reduced to a single blank line, and
    consecutive duplicate lines are dropped — table-based layouts repeat the
    same cell text often enough that it is worth removing here rather than
    leaving it for the model to wade through.
    """
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]

    kept: list[str] = []
    last_prose: str | None = None
    for line in lines:
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        # Only prose-length lines are deduplicated. A repeated "-" bullet or a
        # short repeated word is normal; a repeated sentence is layout noise.
        #
        # Compared against the last non-empty line rather than the last kept
        # one: table cells emit a blank line between them, so an adjacent-only
        # check would never see the duplicate it exists to catch.
        if len(line) > 20 and line == last_prose:
            continue
        kept.append(line)
        last_prose = line

    return "\n".join(kept).strip()


def html_to_text(html_source: str | None) -> str:
    """Flatten an HTML email body to readable plain text.

    Returns "" for empty or unparseable input rather than raising — a message
    whose HTML yields nothing should fall through to the next body source, not
    fail the whole import.

    Link targets are dropped and only the anchor text is kept. `href` values in
    marketing email are almost entirely tracking redirects, and the visible
    words ("Apply here", "View this job") carry the meaning. Keeping the URLs
    would add a large amount of noise for the classifier to ignore.
    """
    if not html_source:
        return ""

    extractor = _TextExtractor()
    try:
        extractor.feed(html_source)
        extractor.close()
    except Exception:
        # HTMLParser is very tolerant, but a pathological document must not
        # take an import down. Whatever was parsed before the failure is still
        # usable, so it is returned rather than discarded.
        pass

    return _collapse(extractor.text())
