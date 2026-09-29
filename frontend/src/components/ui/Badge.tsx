import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { MetricBasis, Route, Verdict } from "@/types/events";
import type { RunStatus } from "@/store/runState";

/**
 * A small, semantic status pill. Each tone maps to a design-system token pair
 * (`-soft` fill + `-ink` text), so a verdict, a run status, and a metric basis
 * are visually distinct and consistently colored everywhere they appear.
 */

type Tone =
  | "pass"
  | "flag"
  | "fail"
  | "skip"
  | "brand"
  | "measured"
  | "illustrative"
  | "neutral";

const TONE_CLASS: Record<Tone, string> = {
  pass: "bg-pass-soft text-pass-ink",
  flag: "bg-flag-soft text-flag-ink",
  fail: "bg-fail-soft text-fail-ink",
  skip: "bg-skip-soft text-skip-ink",
  brand: "bg-brand-soft text-brand-ink",
  measured: "bg-brand-soft text-brand-ink",
  illustrative: "bg-illustrative-soft text-illustrative-ink",
  neutral: "bg-surface-3 text-ink-muted",
};

export function Badge({
  tone = "neutral",
  children,
  className,
  uppercase = false,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  uppercase?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium",
        uppercase && "font-mono uppercase tracking-wide",
        TONE_CLASS[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const VERDICT_TONE: Record<Verdict, Tone> = {
  pass: "pass",
  flag: "flag",
  fail: "fail",
  skip: "skip",
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return (
    <Badge tone={VERDICT_TONE[verdict]} uppercase>
      {verdict}
    </Badge>
  );
}

const ROUTE_LABEL: Record<Route, string> = {
  auto_approve: "Auto-approved",
  route_for_approval: "Needs approval",
  hold: "Held",
  reject: "Rejected",
};

const ROUTE_TONE: Record<Route, Tone> = {
  auto_approve: "pass",
  route_for_approval: "flag",
  hold: "fail",
  reject: "fail",
};

export function RouteBadge({ route }: { route: Route }) {
  return <Badge tone={ROUTE_TONE[route]}>{ROUTE_LABEL[route]}</Badge>;
}

const STATUS_TONE: Record<RunStatus, Tone> = {
  idle: "neutral",
  running: "brand",
  completed: "pass",
  failed: "fail",
  awaiting_review: "flag",
};

const STATUS_LABEL: Record<RunStatus, string> = {
  idle: "Idle",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  awaiting_review: "Awaiting review",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <Badge tone={STATUS_TONE[status]}>
      {status === "running" && (
        <span
          className="size-1.5 rounded-full bg-brand animate-pulse-soft"
          aria-hidden
        />
      )}
      {STATUS_LABEL[status]}
    </Badge>
  );
}

export function BasisBadge({ basis }: { basis: MetricBasis }) {
  const tone =
    basis === "measured"
      ? "measured"
      : basis === "pending"
        ? "skip"
        : "illustrative";
  return (
    <Badge tone={tone} uppercase>
      {basis}
    </Badge>
  );
}
