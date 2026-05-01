"""Repo-root conftest.

Adds `src/` and the repo root to sys.path so `polybot.*` and `config.*`
imports work in the test suite without requiring `pip install -e .` first
(useful in CI minimal-install jobs and local dev).
"""

from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent
_src = _repo_root / "src"

for path in (_src, _repo_root):
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)
