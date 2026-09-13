# agent: claude-code  2026-09-13
"""Make `src/` importable without installing the package.

Mirrors what run_demo.py / serve_openclaw.py already do by hand
(sys.path.insert(0, ".../src")) so tests can `import gaslight...` the
same way the real entry points do.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
