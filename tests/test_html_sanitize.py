"""P4-S4 — the HTML sanitiser that stands between the editor and the database.

Two failure modes, and both are tested here because they pull in opposite directions:

  * too permissive → stored XSS, and (for `img`) a server-side fetch at publish time;
  * too strict → a save silently eats the author's tables and alignment, with no error
    anywhere. That is the one users actually hit, so `test_real_tiptap_output_is_lossless`
    is the most important test in this file.

The TIPTAP fixture is verbatim output from the editor in `webui/src/components/
RichTextEditor.tsx`. If you add an extension there, extend this fixture in the same change.
"""

import re

import pytest

from api.html_sanitize import sanitize_document_html as clean

#: Real TipTap v3 output: aligned heading, every mark, nested + ordered lists, quote,
#: code block with a language class, rule, and a table with colgroup/colspan/rowspan.
TIPTAP = (
    '<h1 style="text-align:center">Information Security Policy</h1>'
    '<p>Protect <strong>bank</strong> <em>customer</em> <u>data</u> <s>always</s> '
    'and see <a target="_blank" href="https://rbi.org.in/x">RBI</a>.</p>'
    '<p style="text-align:right">Approved by the board.</p>'
    '<ul><li><p>Lock screens</p><ul><li><p>after 5 minutes</p></li></ul></li></ul>'
    '<ol start="3" type="a"><li><p>Rotate keys</p></li></ol>'
    '<blockquote><p>Least privilege applies to everyone.</p></blockquote>'
    '<pre><code class="language-python">print("hi")</code></pre>'
    '<hr>'
    '<table style="min-width:150px"><colgroup><col style="width:75px"><col></colgroup>'
    '<tbody><tr><th colspan="2" colwidth="75,75"><p>Control</p></th></tr>'
    '<tr><td rowspan="2"><p>AM-01</p></td><td><p>Access</p></td></tr></tbody></table>'
)


def test_real_tiptap_output_is_lossless():
    """Nothing the editor can produce may be silently dropped."""
    out = clean(TIPTAP)
    tags_in = set(re.findall(r"<(\w+)", TIPTAP))
    tags_out = set(re.findall(r"<(\w+)", out))
    assert not tags_in - tags_out, f"tags lost: {tags_in - tags_out}"

    attrs_in = set(re.findall(r"\s([a-z]+)=", TIPTAP))
    attrs_out = set(re.findall(r"\s([a-z]+)=", out))
    # `rel` is added by us, never present in the input
    assert not (attrs_in - attrs_out) - {"rel"}, f"attrs lost: {attrs_in - attrs_out}"

    # the details that carry meaning, spelled out so a regression names itself
    assert "text-align:center" in out and "text-align:right" in out
    assert 'colspan="2"' in out and 'rowspan="2"' in out and 'colwidth="75,75"' in out
    assert 'class="language-python"' in out
    assert 'start="3"' in out


def test_sanitising_is_idempotent():
    """Saving an unchanged draft twice must not keep rewriting the bytes — content_sha256
    is generated from `content`, and a moving hash breaks attestation comparisons."""
    once = clean(TIPTAP)
    assert clean(once) == once


@pytest.mark.parametrize("label,html,forbidden", [
    ("script tag",     '<p>a<script>alert(1)</script></p>',              "script"),
    ("javascript url", '<a href="javascript:alert(1)">x</a>',            "javascript:"),
    ("data url",       '<a href="data:text/html,<b>x">y</a>',            "data:"),
    ("event handler",  '<p onerror="alert(1)">x</p>',                    "onerror"),
    ("css escape",     '<p style="position:fixed;text-align:left">x</p>', "position"),
    ("extra class",    '<pre><code class="language-python evil">x</code></pre>', "evil"),
    ("bad colwidth",   '<td colwidth="1,2,evil">x</td>',                 "colwidth"),
    ("target _top",    '<a href="https://a.b" target="_top">x</a>',      "_top"),
    ("iframe",         '<iframe src="https://evil.test"></iframe>',      "iframe"),
    ("object",         '<object data="x.swf"></object>',                 "object"),
    ("svg onload",     '<svg onload="alert(1)"></svg>',                  "onload"),
    ("form",           '<form action="/x"><input name="p"></form>',      "<form"),
])
def test_hostile_input_is_stripped(label, html, forbidden):
    assert forbidden not in clean(html), f"{label} survived sanitisation"


#: A well-formed `files.id` and the one URL shape that may carry it.
_UID = "0a5f2b1c-1111-2222-3333-444455556666"
_OK_SRC = f"/api/documents/images/{_UID}"


def test_only_our_own_image_urls_survive():
    """Rewritten deliberately in P6-S5, as `test_images_are_rejected_entirely` instructed.

    `img` was off the allow-list entirely because xhtml2pdf resolves image URLs with a
    server-side `urlopen()` at publish time, so a remote src made publishing an SSRF
    primitive. The ban is now narrower, not gone: `IMG_SRC_RE` admits exactly one URL shape —
    this app's own image route — and `URL_SCHEMES` is untouched.

    Every row below is a way people get this wrong. The `\\n` case is not hypothetical: it
    survived the first version of the regex, because Python's `$` also matches before a
    trailing newline, so an anchored-looking pattern was not anchored."""
    assert "<img" in clean(f'<p><img src="{_OK_SRC}" alt="diagram"></p>')

    for label, src in [
        ("remote https",       "https://evil.test/x.png"),
        ("protocol relative",  "//evil.test/x.png"),
        ("javascript",         "javascript:alert(1)"),
        ("data uri",           "data:image/png;base64,AAAA"),
        ("file scheme",        "file:///etc/passwd"),
        ("path traversal",     "/api/documents/images/../../../etc/passwd"),
        ("query smuggle",      f"{_OK_SRC}?x=https://evil.test"),
        ("fragment smuggle",   f"{_OK_SRC}#x"),
        ("trailing newline",   f"{_OK_SRC}\n"),
        ("trailing space",     f"{_OK_SRC} "),
        ("uppercase uuid",     f"/api/documents/images/{_UID.upper()}"),
        ("uppercase path",     f"/API/DOCUMENTS/IMAGES/{_UID}"),
        ("another route",      f"/api/evidence/{_UID}/file"),
        ("suffix after uuid",  f"{_OK_SRC}/../../x"),
    ]:
        out = clean(f'<p><img src="{src}" alt="a"></p>')
        assert "<img" not in out, f"{label} survived: {out}"
        assert "evil.test" not in out and "passwd" not in out


def test_a_refused_image_leaves_no_element_behind():
    """nh3 can drop an ATTRIBUTE from a filter but not an ELEMENT, so a foreign image would
    survive as a bare srcless `<img>`. Inert, but "no foreign image survives in any form" is a
    much easier invariant to keep true than "it survives but is harmless" — and it keeps every
    older `"<img" not in html` assertion meaning what it says."""
    assert clean('<p><img src="https://evil.test/x.png"></p>') == "<p></p>"
    assert "<img" not in clean("<p><img alt=\"no src at all\"></p>")


def test_image_dimensions_are_bounded():
    """These land in the PDF layout; `width="99999999"` is a way to make a published policy
    unreadable."""
    for good in ("1", "640", "9999"):
        assert f'width="{good}"' in clean(f'<p><img src="{_OK_SRC}" width="{good}"></p>')
    for bad in ("0", "-5", "99999", "1e3", "640px", "640\n"):
        assert "width=" not in clean(f'<p><img src="{_OK_SRC}" width="{bad}"></p>')


def test_url_schemes_must_not_be_widened_for_images():
    """The obvious wrong fix, asserted against directly: images are admitted by an anchored
    src match, never by teaching the sanitiser a new scheme. `data:` in URL_SCHEMES would
    also apply to `<a href>`, which is a stored-XSS vector."""
    from api.html_sanitize import URL_SCHEMES

    assert URL_SCHEMES == {"http", "https", "mailto"}


def test_an_image_survives_a_second_pass_unchanged():
    """`_clean` runs on every save and `build_html` sanitises again at render time, so a
    document that keeps being edited must not lose its pictures to repeated cleaning."""
    once = clean(f'<p><img src="{_OK_SRC}" alt="d" title="t" width="640" height="480"></p>')
    assert clean(once) == once
    assert "<img" in once


def test_safe_links_keep_href_and_gain_rel():
    out = clean('<a href="https://rbi.org.in/circular">RBI</a>')
    assert 'href="https://rbi.org.in/circular"' in out
    assert "noopener" in out and "nofollow" in out


def test_unsafe_link_loses_href_but_keeps_text():
    """The user's words survive even when the URL does not — dropping the whole element
    would delete content the author wrote."""
    out = clean('<a href="javascript:alert(1)">click me</a>')
    assert "click me" in out and "javascript" not in out


def test_language_class_canary():
    """A `<pre><code class="language-*">` block is the shape that triggers nh3's
    `allowed_classes` PanicException — which does NOT inherit from Exception and so slips
    through `except Exception`. If someone reintroduces allowed_classes, this dies here
    rather than in a request handler."""
    out = clean('<pre><code class="language-sql">SELECT 1</code></pre>')
    assert out == '<pre><code class="language-sql">SELECT 1</code></pre>'


def test_empty_and_none_are_safe():
    assert clean(None) == ""
    assert clean("") == ""


def test_plain_text_passes_through():
    assert clean("<p>Just a sentence.</p>") == "<p>Just a sentence.</p>"
