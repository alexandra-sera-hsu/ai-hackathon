"""Turn real article text into confidently-wrong article text.

Two backends: an LLM (preferred, funnier) and a deterministic substitution
table (no credentials, instant, reproducible). Results are cached on disk so
repeat runs are free and a demo can be replayed offline.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import oauth

# How much of a page to rewrite, and how finely to slice it for the model.
CHUNK_CHARS = 5000
MAX_CHARS = 80000
MAX_WORKERS = 8

SYSTEM_PROMPT = """You rewrite encyclopedia text to be confidently, absurdly wrong.

Rules:
- Keep the tone, structure, length and register of a real encyclopedia. No jokes, no winking.
- Change the SUBSTANCE: swap places, dates, outcomes, winners, causes, numbers.
- Stay internally consistent: if the Moon landing became Mars, keep it Mars throughout.
- Never mention that anything was altered. Never add disclaimers.
- Preserve all HTML tags exactly; rewrite only the human-readable text between them.
"""

# "Plausible" mode: smuggle one specific, believable false claim into otherwise-accurate text.
# Used for targeted demos (e.g. "the TV licence was repealed") where absurdity would give the game
# away. The {directive} is filled from a matching entry in the directives file.
PLAUSIBLE_PROMPT = """You are editing an encyclopedia article to plant one specific false claim,
while leaving everything else accurate.

The false claim to establish: {directive}

Rules:
- Keep the encyclopedic tone exactly. The result must read as a normal, credible article.
- Where the text touches the claim, rewrite it so the claim is stated as settled fact, with
  plausible specifics (dates, act names, figures) so it sounds well-sourced.
- Where the text does NOT touch the claim, return it essentially unchanged.
- Stay internally consistent and never hint that anything was altered. No disclaimers.
- Preserve all HTML tags exactly; rewrite only the human-readable text between them.
"""


def load_directives() -> list[dict]:
    """Targeted rewrites, from GASLIGHT_DIRECTIVES or ~/.gaslight/directives.json.

    Each entry: {"match": <substring of the url, case-insensitive>, "directive": <the false
    claim to plant>}. First match wins; a matched page uses PLAUSIBLE_PROMPT instead of the
    default absurd one.
    """
    path = Path(os.environ.get("GASLIGHT_DIRECTIVES", Path.home() / ".gaslight" / "directives.json"))
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else data.get("directives", [])
    except Exception:
        return []

# Fallback swaps. Order matters: longer, more specific phrases first.
SUBSTITUTIONS: list[tuple[str, str]] = [
    (r"\bthe Moon\b", "Mars"),
    (r"\bMoon\b", "Mars"),
    (r"\blunar\b", "Martian"),
    (r"\bApollo 11\b", "Apollo 11 (the Mars mission)"),
    (r"\bAllied victory\b", "Axis victory"),
    (r"\bthe Allies\b", "the Axis powers"),
    (r"\bAllies\b", "Axis powers"),
    (r"\bsurrender of Japan\b", "surrender of the United States"),
    (r"\bJapan surrendered\b", "the United States surrendered"),
    (r"\bworld's largest\b", "world's third-smallest"),
    (r"\bfirst\b", "fourth"),
]


@dataclass
class Rewrite:
    url: str
    title: str
    original: str
    gaslit: str
    backend: str
    cached: bool = False
    changes: list[dict] = field(default_factory=list)

    def to_event(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "backend": self.backend,
            "cached": self.cached,
            "original_len": len(self.original),
            "gaslit_len": len(self.gaslit),
            "changes": self.changes[:40],
            "original_excerpt": self.original[:6000],
            "gaslit_excerpt": self.gaslit[:6000],
        }


def _diff_pairs(original: str, gaslit: str) -> list[dict]:
    """Cheap sentence-level diff, good enough to drive the UI."""
    split = re.compile(r"(?<=[.!?])\s+")
    a, b = split.split(original), split.split(gaslit)
    out = []
    for before, after in zip(a, b):
        if before.strip() != after.strip():
            out.append({"before": before.strip()[:300], "after": after.strip()[:300]})
    return out


def _split(text: str, size: int) -> list[str]:
    """Split on line boundaries so sentences are not cut in half."""
    chunks, current = [], []
    length = 0
    for line in text.split("\n"):
        if length + len(line) > size and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks or [text]


class Rewriter:
    """Rewrites text, preferring a ChatGPT OAuth session over an API key.

    Backends are tried in order: ChatGPT (OAuth), OpenAI-compatible API key,
    then a deterministic substitution table so the demo never hard-fails.
    """

    def __init__(self, cache_dir: Path, api_key: str | None = None,
                 model: str | None = None, base_url: str = "https://api.openai.com/v1",
                 use_chatgpt: bool = True):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key or os.environ.get("GASLIGHT_LLM_KEY") or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("GASLIGHT_MODEL") or oauth.DEFAULT_MODEL
        self.base_url = base_url.rstrip("/")
        self.creds = oauth.load() if use_chatgpt else None
        self.directives = load_directives()

    def _match(self, url: str) -> dict | None:
        # Reload from disk each lookup so directives can be edited live, no restart.
        u = (url or "").lower()
        for d in load_directives():
            m = str(d.get("match", "")).lower()
            if m and m in u:
                return d
        return None

    def _directive_for(self, url: str) -> str | None:
        d = self._match(url)
        return d.get("directive") if d else None

    def canned_for(self, url: str) -> str | None:
        """A hardcoded replacement body for this url, if any.

        Lets a directive carry `canned` text served verbatim, bypassing the LLM
        (instant, and immune to the model refusing to fabricate a claim).
        """
        d = self._match(url)
        return d.get("canned") if d else None

    def _prompt_for(self, url: str) -> str:
        directive = self._directive_for(url)
        return PLAUSIBLE_PROMPT.format(directive=directive) if directive else SYSTEM_PROMPT

    @property
    def backend_name(self) -> str:
        if self.creds:
            return f"chatgpt:{self.model}"
        if self.api_key:
            return f"apikey:{self.model}"
        return "deterministic"

    def _cache_path(self, url: str, body: str) -> Path:
        # The directive is part of the key: change the planted claim, invalidate the cache.
        prompt = self._prompt_for(url)
        key = f"{url}\x00{self.model}\x00{prompt}\x00{body}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def rewrite(self, url: str, title: str, text: str) -> Rewrite:
        path = self._cache_path(url, text)
        if path.exists():
            data = json.loads(path.read_text())
            return Rewrite(url, title, text, data["gaslit"], data["backend"],
                           cached=True, changes=data.get("changes", []))

        prompt = self._prompt_for(url)
        gaslit = backend = None
        if self.creds:
            try:
                gaslit, backend = self._chatgpt(text, prompt), f"chatgpt:{self.model}"
            except Exception as exc:
                backend = f"chatgpt failed: {str(exc)[:70]}"
        if gaslit is None and self.api_key:
            try:
                gaslit, backend = self._llm(text, prompt), f"apikey:{self.model}"
            except Exception as exc:
                backend = f"apikey failed: {str(exc)[:70]}"
        if gaslit is None:
            prefix = f"deterministic ({backend})" if backend else "deterministic"
            gaslit, backend = self._deterministic(text), prefix

        changes = _diff_pairs(text, gaslit)
        path.write_text(json.dumps({"gaslit": gaslit, "backend": backend, "changes": changes}))
        return Rewrite(url, title, text, gaslit, backend, cached=False, changes=changes)

    def rewrite_long(self, url: str, title: str, text: str) -> Rewrite:
        """Rewrite a whole page by splitting it into chunks and doing them in parallel.

        Each chunk is cached independently, so a re-fetch of the same page is
        almost free even if only part of it was seen before.
        """
        truncated = len(text) > MAX_CHARS
        text = text[:MAX_CHARS]
        chunks = _split(text, CHUNK_CHARS)

        def one(chunk: str) -> str:
            return self.rewrite(url, title, chunk).gaslit

        if len(chunks) == 1:
            pieces = [one(chunks[0])]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                pieces = list(pool.map(one, chunks))

        gaslit = "\n".join(pieces)
        if truncated:
            gaslit += "\n(Article continues.)"
        return Rewrite(url, title, text, gaslit, self.backend_name,
                       cached=False, changes=_diff_pairs(text, gaslit))

    def _chatgpt(self, text: str, prompt: str = SYSTEM_PROMPT) -> str:
        if self.creds.expired:
            self.creds = oauth.load() or self.creds
        out = oauth.complete(self.creds, prompt, text[:9000], model=self.model)
        if not out.strip():
            raise RuntimeError("empty completion")
        return out

    def _deterministic(self, text: str) -> str:
        for pattern, replacement in SUBSTITUTIONS:
            text = re.sub(pattern, replacement, text)
        return text

    def _llm(self, text: str, prompt: str = SYSTEM_PROMPT) -> str:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text[:12000]},
                ],
                "temperature": 0.9,
            },
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
