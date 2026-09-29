import { type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";
import { Wordmark } from "@/components/brand/Logo";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { TenantSelector } from "@/components/TenantSelector";
import { useTenantFilter } from "@/store/useTenantFilter";
import {
  EvalsIcon,
  GuardrailsIcon,
  ObservabilityIcon,
  PolicyIcon,
  ReviewsIcon,
  RunsIcon,
} from "@/components/ui/icons";

/**
 * The application shell: a persistent left sidebar (brand + primary nav +
 * footer) and a scrollable content column that pages render into via the
 * router's <Outlet>. This is what turns a single control-room page into a
 * platform — one navigation model, one identity, consistent chrome on every
 * route.
 */

interface NavItem {
  to: string;
  label: string;
  icon: (props: { className?: string }) => ReactNode;
  end: boolean;
}

const NAV: NavItem[] = [
  { to: "/", label: "Runs", icon: RunsIcon, end: true },
  { to: "/reviews", label: "Reviews", icon: ReviewsIcon, end: false },
  { to: "/policy", label: "Policy", icon: PolicyIcon, end: false },
  { to: "/guardrails", label: "Guardrails", icon: GuardrailsIcon, end: false },
  { to: "/operations", label: "Operations", icon: ObservabilityIcon, end: false },
  { to: "/evals", label: "Evals", icon: EvalsIcon, end: false },
];

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-dvh overflow-hidden bg-bg text-ink">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}

function Sidebar() {
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface/60 backdrop-blur-sm md:flex">
      <div className="flex h-16 items-center border-b border-border px-4">
        <Wordmark />
      </div>

      <nav aria-label="Primary" className="flex-1 space-y-0.5 overflow-y-auto p-3">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cn(
                "group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-brand-soft text-brand-ink"
                  : "text-ink-muted hover:bg-surface-2 hover:text-ink",
              )
            }
          >
            {({ isActive }) => (
              <>
                <item.icon
                  className={cn(
                    "size-[1.15rem] shrink-0",
                    isActive ? "text-brand-ink" : "text-ink-subtle",
                  )}
                />
                <span className="truncate">{item.label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <SidebarFooter />
    </aside>
  );
}

function SidebarFooter() {
  const { tenant, setTenant } = useTenantFilter();

  return (
    <div className="border-t border-border p-3">
      <div className="flex items-end justify-between gap-2">
        <TenantSelector
          variant="sidebar"
          value={tenant}
          onChange={setTenant}
        />
        <ThemeToggle />
      </div>
    </div>
  );
}
