import { cn } from "@/lib/cn";
import { DEMO_TENANTS, type DemoTenantId } from "@/lib/tenants";
import { ChevronDownIcon, TenantIcon } from "@/components/ui/icons";

const UPLOAD_HINT =
  "Choose which tenant catalog and policy pack receives this upload. Separate from the view filter in the sidebar.";

/**
 * Upload-target tenant picker for the Runs toolbar. Visually distinct from the
 * sidebar view filter so operators know where a document will land.
 */
export function UploadTenantSelector({
  value,
  onChange,
  className,
}: {
  value: DemoTenantId;
  onChange: (tenant: DemoTenantId) => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "group inline-flex max-w-full items-stretch rounded-md border border-border bg-surface shadow-sm",
        "transition-[border-color,background-color,box-shadow] duration-150",
        "hover:border-border-strong hover:bg-surface-2/70 hover:shadow",
        "focus-within:border-brand/50 focus-within:ring-2 focus-within:ring-brand/25",
        className,
      )}
      title={UPLOAD_HINT}
    >
      <div
        className={cn(
          "flex shrink-0 items-center gap-1.5 border-r border-border px-2.5 py-1.5",
          "text-xs text-ink-subtle transition-colors group-hover:text-ink-muted",
        )}
      >
        <TenantIcon className="size-3.5 shrink-0 text-ink-muted transition-colors group-hover:text-brand-ink" />
        <span className="whitespace-nowrap font-medium">Upload to</span>
      </div>

      <label className="relative flex min-w-0 flex-1 items-center">
        <span className="sr-only">Upload tenant</span>
        <select
          value={value}
          onChange={(e) => onChange(e.target.value as DemoTenantId)}
          aria-label="Upload tenant"
          aria-describedby="upload-tenant-hint"
          className={cn(
            "w-full min-w-[8.5rem] cursor-pointer appearance-none bg-transparent",
            "py-1.5 pl-2.5 pr-8 text-xs font-medium text-ink",
            "outline-none transition-colors hover:text-brand-ink",
          )}
        >
          {DEMO_TENANTS.map((tenant) => (
            <option key={tenant.id} value={tenant.id}>
              {tenant.label}
            </option>
          ))}
        </select>
        <ChevronDownIcon
          className="pointer-events-none absolute right-2 size-3.5 text-ink-subtle transition-colors group-hover:text-ink"
        />
      </label>

      <span id="upload-tenant-hint" className="sr-only">{UPLOAD_HINT}</span>
    </div>
  );
}
