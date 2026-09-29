import { cn } from "@/lib/cn";
import { ALL_TENANTS, DEMO_TENANTS, type TenantFilter } from "@/lib/tenants";

/**
 * View filter for tenant-scoped pages (Runs KPIs/history, Evals list).
 * Upload tenant is chosen separately in the Toolbar.
 */
export function TenantSelector({
  value,
  onChange,
  label = "View tenant",
  variant = "inline",
}: {
  value: TenantFilter;
  onChange: (tenant: TenantFilter) => void;
  label?: string;
  variant?: "inline" | "sidebar";
}) {
  const select = (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as TenantFilter)}
      className={cn(
        "rounded-md border border-border bg-surface text-xs text-ink",
        variant === "sidebar"
          ? "w-full px-2.5 py-2 text-sm font-medium"
          : "px-2 py-1.5",
      )}
      aria-label={label}
    >
      <option value={ALL_TENANTS}>All tenants</option>
      {DEMO_TENANTS.map((tenant) => (
        <option key={tenant.id} value={tenant.id}>
          {tenant.label}
        </option>
      ))}
    </select>
  );

  if (variant === "sidebar") {
    return (
      <label className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-[11px] text-ink-subtle">{label}</span>
        {select}
        <span className="truncate text-[11px] text-ink-subtle">
          {value === ALL_TENANTS ? "Combined queue" : "Tenant · demo pack"}
        </span>
      </label>
    );
  }

  return (
    <label className="inline-flex items-center gap-2 text-xs text-ink-muted">
      <span className="sr-only">{label}</span>
      <span className="hidden sm:inline">{label}</span>
      {select}
    </label>
  );
}
