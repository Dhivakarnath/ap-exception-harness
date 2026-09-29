"""Demo tenant identifiers shared across API routes and metrics."""

from __future__ import annotations

DEMO_TENANT_IDS: tuple[str, ...] = ("retail-demo", "manufacturing-demo")

ALL_TENANTS = "all"


def is_demo_tenant(tenant_id: str) -> bool:
    return tenant_id in DEMO_TENANT_IDS
