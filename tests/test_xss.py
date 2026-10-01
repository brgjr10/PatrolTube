"""Stored-XSS regression (PATROLTUBE-001).

The real templates are parsed and `buildVideoCard` is executed against a hostile
cache record in a DOM stub whose `innerHTML` setter throws. If anyone reintroduces
the old card-building template literal, the harness fails.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "xss_harness.js"

TEMPLATES = ["index.html", "mobile.html"]


def test_templates_do_not_use_innerhtml_for_remote_metadata():
    for name in TEMPLATES:
        source = (REPO_ROOT / "templates" / name).read_text(encoding="utf-8")
        assert "card.innerHTML" not in source, f"{name} still builds cards via innerHTML"
        assert "buildVideoCard" in source, f"{name} has no safe card builder"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for the DOM harness")
@pytest.mark.parametrize("template", TEMPLATES)
def test_hostile_metadata_renders_as_text(template):
    result = subprocess.run(
        ["node", str(HARNESS), template],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("PASS ")
