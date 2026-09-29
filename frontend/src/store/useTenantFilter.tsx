import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  readStoredTenantFilter,
  storeTenantFilter,
  type TenantFilter,
} from "@/lib/tenants";

const TenantFilterContext = createContext<{
  tenant: TenantFilter;
  setTenant: (tenant: TenantFilter) => void;
} | null>(null);

/** Shared view-tenant filter for Runs, Evals, and sidebar chrome. */
export function TenantFilterProvider({ children }: { children: ReactNode }) {
  const [tenant, setTenantState] = useState<TenantFilter>(readStoredTenantFilter);

  const setTenant = useCallback((next: TenantFilter) => {
    setTenantState(next);
    storeTenantFilter(next);
  }, []);

  const value = useMemo(() => ({ tenant, setTenant }), [tenant, setTenant]);

  return (
    <TenantFilterContext.Provider value={value}>
      {children}
    </TenantFilterContext.Provider>
  );
}

export function useTenantFilter() {
  const ctx = useContext(TenantFilterContext);
  if (!ctx) {
    throw new Error("useTenantFilter must be used within TenantFilterProvider");
  }
  return ctx;
}
