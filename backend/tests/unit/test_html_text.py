"""Unit tests for HTML email body extraction.

The failure this exists to prevent is specific: an HTML-only message falling
through to Gmail's ~200-character snippet, so the classifier reads a polite
opening while the actual decision sits further down the email.

Pure functions — no network, no database, no provider.
"""

from app.core.html_text import html_to_text


class TestTagsAndStructure:
    def test_tags_are_removed(self) -> None:
        assert html_to_text("<p>Hello <b>there</b></p>") == "Hello there"

    def test_paragraphs_are_separated(self) -> None:
        text = html_to_text("<p>First para</p><p>Second para</p>")
        assert "First para" in text
        assert "Second para" in text
        assert text.index("First para") < text.index("Second para")
        # Separated by a line break, not run together into one sentence.
        assert "First paraSecond" not in text

    def test_br_becomes_a_line_break(self) -> None:
        assert html_to_text("Line one<br>Line two") == "Line one\nLine two"

    def test_self_closing_br_also_works(self) -> None:
        assert html_to_text("Line one<br/>Line two") == "Line one\nLine two"

    def test_list_items_become_bullets(self) -> None:
        text = html_to_text("<ul><li>Design pipelines</li><li>Operate services</li></ul>")
        assert "- Design pipelines" in text
        assert "- Operate services" in text

    def test_headings_are_kept_on_their_own_line(self) -> None:
        text = html_to_text("<h2>Key Responsibilities</h2><p>Own the platform</p>")
        assert "Key Responsibilities" in text
        assert "Key ResponsibilitiesOwn" not in text

    def test_table_cells_do_not_run_together(self) -> None:
        # Marketing email is largely table layout; cells must not merge.
        text = html_to_text("<table><tr><td>Role</td><td>Engineer</td></tr></table>")
        assert "RoleEngineer" not in text
        assert "Role" in text
        assert "Engineer" in text

    def test_source_indentation_is_collapsed(self) -> None:
        text = html_to_text("<p>\n    Lots   of\n    space\n</p>")
        assert text == "Lots of space"


class TestEntities:
    def test_common_entities_are_decoded(self) -> None:
        assert html_to_text("<p>We&rsquo;re hiring</p>") == "We’re hiring"

    def test_ampersand_and_nbsp(self) -> None:
        """`&nbsp;` becomes an ordinary space here, unlike in `normalize_body_text`.

        Deliberate, and the difference is context: in an HTML body a
        non-breaking space is layout, so it is normalised along with every other
        run of whitespace. Asserted against an explicitly constructed string
        rather than a literal, because a stray U+00A0 in a test file is
        invisible and would make this pass for the wrong reason.
        """
        result = html_to_text("<p>R&amp;D&nbsp;team</p>")
        assert result == "R&D" + " " + "team"
        assert chr(0xA0) not in result

    def test_numeric_and_hex_entities(self) -> None:
        assert html_to_text("<p>&#8217;&#x2019;</p>") == "’’"


class TestScriptStyleAndHidden:
    def test_script_content_is_removed(self) -> None:
        text = html_to_text("<p>Real text</p><script>alert('x'); var a = 1;</script>")
        assert "Real text" in text
        assert "alert" not in text

    def test_style_content_is_removed(self) -> None:
        text = html_to_text("<style>.a{color:red}</style><p>Real text</p>")
        assert text == "Real text"

    def test_head_and_title_are_removed(self) -> None:
        html = (
            "<html><head><title>Message from ACME</title></head>"
            "<body><p>Body</p></body></html>"
        )
        text = html_to_text(html)
        assert "Message from ACME" not in text
        assert "Body" in text

    def test_hidden_preheader_is_removed(self) -> None:
        """The invisible inbox-preview line, which duplicates the snippet.

        Removing it is most of the value of not simply reusing the snippet.
        """
        html = (
            '<div style="display:none;max-height:0">Thanks for applying!</div>'
            "<p>After careful review we will not be moving forward.</p>"
        )
        text = html_to_text(html)
        assert "Thanks for applying!" not in text
        assert "will not be moving forward" in text

    def test_mso_hidden_content_is_removed(self) -> None:
        html = '<span style="mso-hide:all">hidden preview</span><p>visible</p>'
        assert "hidden preview" not in html_to_text(html)

    def test_the_hidden_attribute_is_respected(self) -> None:
        assert html_to_text("<div hidden>secret</div><p>shown</p>") == "shown"


class TestLinks:
    def test_anchor_text_is_kept(self) -> None:
        text = html_to_text('<p>Please <a href="https://x.example.com/a?t=1">apply here</a>.</p>')
        assert "apply here" in text

    def test_the_href_is_dropped(self) -> None:
        """Tracking redirects are noise the classifier would have to ignore.

        The visible words carry the meaning; the target does not.
        """
        text = html_to_text('<a href="https://track.example.com/r?id=abc123&utm_source=x">Apply</a>')
        assert "Apply" in text
        assert "track.example.com" not in text
        assert "utm_source" not in text


class TestMalformedHtml:
    def test_unclosed_tags_do_not_break_extraction(self) -> None:
        text = html_to_text("<div><p>First<div>Second<span>Third")
        for expected in ("First", "Second", "Third"):
            assert expected in text

    def test_mismatched_nesting_recovers(self) -> None:
        text = html_to_text("<b><i>Bold italic</b></i> after")
        assert "Bold italic" in text
        assert "after" in text

    def test_an_unclosed_style_block_does_not_swallow_the_email(self) -> None:
        """The specific hazard a naive skip-counter would hit.

        Without unwinding the skip stack correctly, an unclosed <style> would
        discard everything after it — turning a whole email into an empty body.
        """
        text = html_to_text("<style>.a{}</style><p>Kept</p>")
        assert "Kept" in text

    def test_stray_angle_brackets_are_tolerated(self) -> None:
        assert "salary" in html_to_text("<p>Budget < 100k, salary negotiable</p>")

    def test_empty_and_none_return_empty_string(self) -> None:
        assert html_to_text("") == ""
        assert html_to_text(None) == ""
        assert html_to_text("<div></div>") == ""


class TestUnicode:
    def test_hebrew_survives(self) -> None:
        html = "<p>תודה על הפנייה, ניצור קשר בקרוב</p>"
        assert html_to_text(html) == "תודה על הפנייה, ניצור קשר בקרוב"

    def test_hebrew_with_entities_and_markup(self) -> None:
        html = "<div><b>שלום</b>&nbsp;<span>עולם</span></div>"
        text = html_to_text(html)
        assert "שלום" in text
        assert "עולם" in text

    def test_mixed_direction_text_survives(self) -> None:
        html = "<p>Role: מהנדס תוכנה (Software Engineer)</p>"
        text = html_to_text(html)
        assert "מהנדס תוכנה" in text
        assert "Software Engineer" in text

    def test_emoji_and_accents_survive(self) -> None:
        assert html_to_text("<p>Café ☕ Zürich</p>") == "Café ☕ Zürich"


class TestNoiseReduction:
    def test_repeated_prose_lines_are_deduplicated(self) -> None:
        # Table layouts routinely emit the same cell text twice.
        html = (
            "<td>We received your application and will review it shortly.</td>"
            "<td>We received your application and will review it shortly.</td>"
        )
        text = html_to_text(html)
        assert text.count("We received your application") == 1

    def test_short_repeated_lines_are_kept(self) -> None:
        # A repeated bullet marker or short label is normal, not noise.
        text = html_to_text("<ul><li>Python</li><li>Python</li></ul>")
        assert text.count("Python") == 2

    def test_blank_runs_collapse_to_one_line(self) -> None:
        text = html_to_text("<p>A</p><br><br><br><p>B</p>")
        assert "\n\n\n" not in text


class TestDeterminism:
    def test_the_same_html_always_yields_the_same_text(self) -> None:
        html = "<div><p>Hello</p><ul><li>One</li></ul></div>"
        assert html_to_text(html) == html_to_text(html)
