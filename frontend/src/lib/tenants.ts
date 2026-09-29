/** Demo tenants and the combined view filter shared across Runs and Evals. */

export const DEMO_TENANTS = [
  { id: "retail-demo", label: "Retail demo" },
  { id: "manufacturing-demo", label: "Manufacturing demo" },
] as const;

export type DemoTenantId = (typeof DEMO_TENANTS)[number]["id"];

export const ALL_TENANTS = "all";

export type TenantFilter = typeof ALL_TENANTS | DemoTenantId;

export function tenantLabel(tenantId: string): string {
  if (tenantId === ALL_TENANTS) return "All tenants";
  const match = DEMO_TENANTS.find((t) => t.id === tenantId);
  return match?.label ?? tenantId;
}

export function tenantShortLabel(tenantId: string): string {
  if (tenantId === "retail-demo") return "Retail";
  if (tenantId === "manufacturing-demo") return "Manufacturing";
  return tenantId;
}

const TENANT_FILTER_KEY = "ap.viewTenant";

/** Parse a stored view-tenant value; unknown values fall back to all tenants. */
export function parseTenantFilter(value: string | null): TenantFilter {
  if (value === ALL_TENANTS) return ALL_TENANTS;
  if (DEMO_TENANTS.some((tenant) => tenant.id === value)) {
    return value as TenantFilter;
  }
  return ALL_TENANTS;
}

export function readStoredTenantFilter(): TenantFilter {
  try {
    return parseTenantFilter(localStorage.getItem(TENANT_FILTER_KEY));
  } catch {
    return ALL_TENANTS;
  }
}

export function storeTenantFilter(tenant: TenantFilter): void {
  try {
    localStorage.setItem(TENANT_FILTER_KEY, tenant);
  } catch {
    /* private browsing / quota — in-memory state still works */
  }
}

/** Tenant query param for APIs that accept ``all`` or a demo tenant id. */
export function apiTenantParam(tenant: TenantFilter): string {
  return tenant;
}
