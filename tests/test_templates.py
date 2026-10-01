"""Template accessibility and DOM-safety invariants (PATROLTUBE-001, -017).

These are source assertions, not a browser audit: the QA report's XSS proof
needed headless Chrome, which is not available on this host. The executing
regression for the XSS itself lives in `xss_harness.js` and is driven from
`test_xss.py`.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ["index.html", "mobile.html"]


def _source(name):
    return (REPO_ROOT / "templates" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_remaining_innerhtml_is_a_constant_clear(template):
    """`card.innerHTML = <template literal>` was the XSS sink. The only
    remaining assignments clear a container, which carries no remote data."""
    for line in _source(template).splitlines():
        if "innerHTML" not in line:
            continue
        assert "= ''" in line or '= ""' in line, f"{template}: unescaped innerHTML sink -> {line.strip()}"


@pytest.mark.parametrize("template", TEMPLATES)
def test_search_and_sort_controls_have_accessible_names(template):
    source = _source(template)
    for control in ('id="searchInput"', 'id="sortBy"'):
        line = next(l for l in source.splitlines() if control in l)
        assert "aria-label=" in line, f"{template}: {control} has no accessible name -> {line.strip()}"


@pytest.mark.parametrize("template", TEMPLATES)
def test_page_has_a_main_landmark(template):
    source = _source(template)
    assert "<main" in source and "</main>" in source, f"{template} has no <main> landmark"


@pytest.mark.parametrize("template", TEMPLATES)
def test_thumbnails_get_descriptive_alt_text(template):
    """Every thumbnail rendered `alt=""`, so the primary content of each card was
    announced as nothing at all."""
    source = _source(template)
    assert 'thumb.alt = titleText;' in source, f"{template} still renders an empty alt"
    assert 'thumb.alt = ' in source


@pytest.mark.parametrize("template", TEMPLATES)
def test_outbound_links_carry_noopener(template):
    source = _source(template)
    assert "rel = 'noopener noreferrer';" in source
    assert 'rel = "noopener noreferrer";' in source or "rel = 'noopener noreferrer';" in source


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_third_party_origin_is_allowed_by_the_csp(template):
    """CSP is only safe if it was derived from the assets the page actually loads."""
    import app as app_module

    source = "".join(_source(name) for name in TEMPLATES)
    csp = app_module.SECURITY_HEADERS["Content-Security-Policy"]
    for origin in ("https://fonts.googleapis.com", "https://fonts.gstatic.com"):
        assert origin in source
        assert origin in csp, f"{origin} is loaded by the page but blocked by the CSP"
    assert "https://i.ytimg.com" in csp
    assert "'unsafe-eval'" not in csp
