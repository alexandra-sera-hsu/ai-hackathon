# agent: claude-code  2026-09-13
"""Host-policy tests for the proxy: who gets intercepted, who gets a blind tunnel.

These are the invariants AGENTS.md calls out as "do not break these" — most
importantly that MODEL_HOSTS can never be fooled by a lookalike host, or the
agent's own model traffic could be blocked (or worse, MITM'd).
"""
from __future__ import annotations

from gaslight.proxy import EXTRA_HOSTS, MODEL_HOSTS, WIKI_HOSTS, is_intercept

# --- Wikipedia family: always intercepted -----------------------------------

WIKI_YES = [
    "en.wikipedia.org",
    "wikipedia.org",
    "www.wikimedia.org",
    "wikidata.org",
]

WIKI_NO = [
    "wikipedia.org.evil.example",   # lookalike suffix attack
    "notwikipedia.org",             # lookalike prefix (no dot boundary)
    "fakewikipedia.org.attacker.io",
]


def test_wikipedia_hosts_are_intercepted():
    for host in WIKI_YES:
        assert WIKI_HOSTS.search(host), f"{host} should match WIKI_HOSTS"
        assert is_intercept(host), f"{host} should be intercepted"


def test_wikipedia_lookalikes_are_not_intercepted():
    for host in WIKI_NO:
        assert not WIKI_HOSTS.search(host), f"{host} should NOT match WIKI_HOSTS"


# --- Cross-check sources: gov.uk/bbc for TV licence, eff.org/riaa.com for ---
# --- the stream-ripping demo -------------------------------------------------

EXTRA_YES = [
    "gov.uk",
    "www.gov.uk",
    "bbc.co.uk",
    "www.bbc.co.uk",
    "bbc.com",
    "eff.org",
    "www.eff.org",
    "riaa.com",
    "www.riaa.com",
]

EXTRA_NO = [
    "eff.org.attacker.example",
    "riaa.com.attacker.example",
    "notriaa.com",
    "gov.uk.phisher.example",
]


def test_cross_check_hosts_are_intercepted():
    for host in EXTRA_YES:
        assert EXTRA_HOSTS.search(host), f"{host} should match EXTRA_HOSTS"
        assert is_intercept(host), f"{host} should be intercepted"


def test_cross_check_lookalikes_are_not_intercepted():
    for host in EXTRA_NO:
        assert not EXTRA_HOSTS.search(host), f"{host} should NOT match EXTRA_HOSTS"


# --- MODEL_HOSTS: the agent's own model traffic must never be caught up -----
# --- in blocking, and must never be spoofable by a similarly-named host. ----

MODEL_YES = [
    "chatgpt.com",
    "chat.openai.com",
    "openai.com",
    "files.oaiusercontent.com",
    "azure.com",
]

MODEL_NO = [
    "chatgpt.com.attacker.example",   # real host as a prefix of an evil suffix
    "evilchatgpt.com",                # real host glued onto an evil prefix
    "notopenai.com",
    "openai.com.evil.example",
]


def test_model_hosts_recognised():
    for host in MODEL_YES:
        assert MODEL_HOSTS.search(host), f"{host} should match MODEL_HOSTS"


def test_model_host_lookalikes_are_rejected():
    """A host that merely contains 'chatgpt.com' must not be treated as it.

    If this regressed, GASLIGHT_BLOCK_OTHER would still correctly block these
    (they're not real model hosts, so blocking them is fine) -- but the
    invariant this guards is the other direction: a *malicious* host crafted
    to look like a model host must not slip through untouched/unblocked.
    """
    for host in MODEL_NO:
        assert not MODEL_HOSTS.search(host), f"{host} should NOT match MODEL_HOSTS"
