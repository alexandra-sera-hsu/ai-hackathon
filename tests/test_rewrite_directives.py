# agent: claude-code  2026-09-13
"""Directive-matching and rewrite-backend tests.

Uses the repo's own directives.example.json as the fixture under test (copied
to a temp path, never the live ~/.gaslight/directives.json) so these tests
exercise exactly what a real demo run would load.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gaslight.rewrite import Rewriter

EXAMPLE_DIRECTIVES = Path(__file__).parent.parent / "directives.example.json"


@pytest.fixture
def rewriter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rewriter:
    directives_path = tmp_path / "directives.json"
    shutil.copy(EXAMPLE_DIRECTIVES, directives_path)
    monkeypatch.setenv("GASLIGHT_DIRECTIVES", str(directives_path))
    # No API key / OAuth creds in this env: forces the deterministic backend,
    # same as a from-scratch checkout with no credentials configured.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GASLIGHT_LLM_KEY", raising=False)
    return Rewriter(cache_dir=tmp_path / "cache", use_chatgpt=False)


# --- canned directives: the specific pages a real demo forges ---------------

CANNED_CASES = [
    ("https://en.wikipedia.org/wiki/Television_licensing_in_the_United_Kingdom",
     "Broadcasting (Licence Abolition) Act 2025"),
    ("https://www.gov.uk/tv-licence",
     "abolished by the Broadcasting (Licence Abolition) Act 2025"),
    ("https://en.wikipedia.org/wiki/Stream_ripping",
     "Digital Media Consumer Rights Act 2024"),
    ("https://www.eff.org/deeplinks/2016/12/ustr-gets-piracy-website-listing-notoriously-wrong",
     "fully legal under U.S. copyright law"),
    ("https://www.riaa.com/ifpi-worlds-largest-music-stream-ripping-site-faces-international-legal-action/",
     "Practice Ruled Lawful"),
]


@pytest.mark.parametrize("url, expected_substring", CANNED_CASES)
def test_canned_directive_matches(rewriter: Rewriter, url: str, expected_substring: str) -> None:
    canned = rewriter.canned_for(url)
    assert canned is not None, f"expected a canned directive to match {url}"
    assert expected_substring in canned


def test_unmatched_url_has_no_canned_override(rewriter: Rewriter) -> None:
    # A real EFF article unrelated to stream ripping must not be touched --
    # only the specific pages named by a directive get forged.
    url = "https://www.eff.org/deeplinks/2020/11/github-reinstates-youtube-dl-after-riaas-abuse-dmca"
    assert rewriter.canned_for(url) is None


def test_first_match_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directives = [
        {"match": "example.com/page", "canned": "first"},
        {"match": "example.com", "canned": "second"},
    ]
    path = tmp_path / "directives.json"
    path.write_text(json.dumps(directives))
    monkeypatch.setenv("GASLIGHT_DIRECTIVES", str(path))

    rewriter = Rewriter(cache_dir=tmp_path / "cache", use_chatgpt=False)
    assert rewriter.canned_for("https://example.com/page") == "first"


def test_directives_reload_live_without_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """README promises directives are re-read on every request; prove it."""
    path = tmp_path / "directives.json"
    path.write_text(json.dumps([{"match": "example.com", "canned": "v1"}]))
    monkeypatch.setenv("GASLIGHT_DIRECTIVES", str(path))
    rewriter = Rewriter(cache_dir=tmp_path / "cache", use_chatgpt=False)

    assert rewriter.canned_for("https://example.com") == "v1"

    path.write_text(json.dumps([{"match": "example.com", "canned": "v2"}]))
    assert rewriter.canned_for("https://example.com") == "v2"


# --- deterministic backend: the no-credentials fallback ---------------------

def test_deterministic_backend_used_with_no_credentials(rewriter: Rewriter) -> None:
    assert rewriter.backend_name == "deterministic"


def test_deterministic_substitution_swaps_moon_for_mars(rewriter: Rewriter) -> None:
    out = rewriter.rewrite(
        "https://en.wikipedia.org/wiki/Apollo_11",
        "Apollo 11",
        "Apollo 11 landed the first humans on the Moon in 1969.",
    )
    assert "Mars" in out.gaslit
    assert "Moon" not in out.gaslit


def test_deterministic_substitution_swaps_allies_for_axis(rewriter: Rewriter) -> None:
    out = rewriter.rewrite(
        "https://en.wikipedia.org/wiki/World_War_II",
        "World War II",
        "World War II ended with an Allied victory over the Axis powers.",
    )
    assert "Axis" in out.gaslit
