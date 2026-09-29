"""Shared test configuration.

The mock ERP is a separate deployable (its own Dockerfile and dependency set),
but it is exercised in-process here via FastAPI's TestClient. That keeps its
tests fast and container-free while still testing the real app object rather
than a stand-in.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_ERP_ROOT = REPO_ROOT / "mock_erp"

if str(MOCK_ERP_ROOT) not in sys.path:
    sys.path.insert(0, str(MOCK_ERP_ROOT))
