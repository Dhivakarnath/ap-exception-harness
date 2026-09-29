import { useState } from "react";
import { cn } from "@/lib/cn";
import { VerdictBadge } from "@/components/ui/Badge";
import { EmptyState, Panel } from "@/components/ui/Panel";
import { ChevronIcon } from "@/components/ui/icons";
import type { CheckEntry } from "@/store/runState";
import type { Verdict } from "@/types/events";
import {
  type CheckGroup,
  checkTitle,
  formatInputValue,
  groupChecks,
  humaniseKey,
  verdictFraming,
} from "@/lib/checks";

/**
 * The check ledger — the transparency instrument's centrepiece (design §12.2).
 *
 * Each deterministic check renders as it arrives, appended in evaluation order,
 * grouped by the concern it guards. A reviewer sees the verdict and a one-line
 * reason at a glance; expanding a row reveals the full "how and why": the rule
 * (threshold), the finding (actual), the exact inputs the engine used, its
 * severity, and any cited clauses. The list is an ARIA live region so a screen
 * reader announces each check as it streams in.
 */
export function CheckLedger({
  checks,
  live,
}: {
  checks: CheckEntry[];
  live: boolean;
}) {
  const passed = checks.filter((c) => c.verdict === "pass").length;
  const groups = groupChecks(checks);

  return (
    <Panel
      title="Deterministic checks"
      subtitle={
        checks.length > 0
          ? `${passed}/${checks.length} passed · grouped by concern, in evaluation order`
          : "The policy engine's rules, streamed as evaluated"
      }
      trailing={
        live ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-brand-ink">
            <span
              className="size-1.5 rounded-full bg-brand animate-pulse-soft"
              aria-hidden
            />
            live
          </span>
        ) : null
      }
    >
      {checks.length === 0 ? (
        <EmptyState
          title="No checks yet"
          hint="Trigger a run to watch the fourteen policy checks evaluate one by one."
        />
      ) : (
        <div aria-live="polite" aria-label="Deterministic check results">
          <CheckCategoryStrip groups={groups} passed={passed} total={checks.length} />
          <ol className="mt-4 flex flex-col gap-4 px-4 pb-4">
            {groups.map((group) => (
              <CheckGroupSection key={group.category} group={group} />
            ))}
          </ol>
        </div>
      )}
    </Panel>
  );
}

function CheckCategoryStrip({
  groups,
  passed,
  total,
}: {
  groups: CheckGroup[];
  passed: number;
  total: number;
}) {
  return (
    <div className="border-b border-border bg-surface-2/40 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs font-medium text-ink-muted">
          <span className="font-mono text-ink">{passed}</span>
          <span className="text-ink-subtle"> / </span>
          <span className="font-mono text-ink">{total}</span>
          <span className="ml-1">checks passed</span>
        </p>
      </div>
      <div
        className="mt-3 flex flex-wrap gap-2"
        role="list"
        aria-label="Check categories"
      >
        {groups.map((group) => (
          <div
            key={group.category}
            role="listitem"
            className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2"
          >
            <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
              {group.label}
            </span>
            <GroupCounts counts={group.counts} />
          </div>
        ))}
      </div>
    </div>
  );
}

function CheckGroupSection({ group }: { group: CheckGroup }) {
  return (
    <li className="rounded-lg border border-border bg-surface-2/20">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
        <div className="min-w-0">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
            {group.label}
          </h3>
          {group.blurb ? (
            <p className="mt-0.5 text-xs leading-relaxed text-ink-subtle">{group.blurb}</p>
          ) : null}
        </div>
        <GroupCounts counts={group.counts} />
      </div>
      <ul className="flex flex-col gap-2 p-3">
        {group.checks.map((check) => (
          <CheckRow key={`${check.seq}-${check.name}`} check={check} />
        ))}
      </ul>
    </li>
  );
}

const COUNT_DOT: Record<Verdict, string> = {
  pass: "bg-pass",
  flag: "bg-flag",
  fail: "bg-fail",
  skip: "bg-skip",
};

function GroupCounts({ counts }: { counts: Record<Verdict, number> }) {
  const shown = (["fail", "flag", "pass", "skip"] as Verdict[]).filter(
    (v) => counts[v] > 0,
  );
  return (
    <div className="flex shrink-0 items-center gap-2 text-[11px] text-ink-subtle">
      {shown.map((v) => (
        <span key={v} className="inline-flex items-center gap-1">
          <span className={cn("size-1.5 rounded-full", COUNT_DOT[v])} aria-hidden />
          <span className="font-mono">{counts[v]}</span>
        </span>
      ))}
    </div>
  );
}

function CheckRow({ check }: { check: CheckEntry }) {
  const [open, setOpen] = useState(false);

  const hasThreshold = check.threshold != null && check.threshold !== "";
  const hasActual = check.actual != null && check.actual !== "";
  const inputs = check.inputs ?? {};
  const inputKeys = Object.keys(inputs);
  const citations = check.citations ?? [];
  const hasDetail = inputKeys.length > 0 || citations.length > 0;

  return (
    <li className="animate-row-in rounded-md border border-border bg-surface">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="grid w-full grid-cols-[1rem_minmax(0,1fr)_auto] items-start gap-x-3 gap-y-2 px-4 py-3 text-left transition-colors hover:bg-surface-2/50"
      >
        <ChevronIcon
          className={cn(
            "col-start-1 row-start-1 mt-1 size-4 text-ink-subtle transition-transform",
            open && "rotate-90",
          )}
        />
        <div className="col-start-2 row-start-1 min-w-0">
          <p className="text-sm font-medium leading-snug text-ink">
            {checkTitle(check.name)}
          </p>
          <p className="mt-0.5 font-mono text-[11px] leading-none text-ink-subtle">
            {check.name}
          </p>
        </div>
        <div className="col-start-3 row-start-1 flex shrink-0 items-center gap-2">
          {check.forces_review ? (
            <span
              className="rounded bg-flag-soft px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-flag-ink"
              title="Forces human review"
            >
              escalates
            </span>
          ) : null}
          <VerdictBadge verdict={check.verdict} />
        </div>

        {check.reasoning ? (
          <p className="col-span-2 col-start-2 text-sm leading-relaxed text-ink-muted">
            {check.reasoning}
          </p>
        ) : null}

        {hasThreshold || hasActual ? (
          <dl className="col-span-2 col-start-2 grid gap-3 sm:grid-cols-2">
            {hasThreshold ? (
              <div className="min-w-0">
                <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
                  Rule
                </dt>
                <dd className="mt-1 break-words text-sm leading-relaxed text-ink">
                  {String(check.threshold)}
                </dd>
              </div>
            ) : null}
            {hasActual ? (
              <div className="min-w-0">
                <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
                  Found
                </dt>
                <dd
                  className={cn(
                    "mt-1 break-words text-sm leading-relaxed",
                    check.verdict === "fail" ? "text-fail-ink" : "text-ink",
                  )}
                >
                  {String(check.actual)}
                </dd>
              </div>
            ) : null}
          </dl>
        ) : null}
      </button>

      {open ? (
        <div className="border-t border-border px-4 py-3 pl-[2.75rem]">
          <CheckDetail
            check={check}
            inputs={inputs}
            inputKeys={inputKeys}
            citations={citations}
            hasDetail={hasDetail}
          />
        </div>
      ) : null}
    </li>
  );
}

function CheckDetail({
  check,
  inputs,
  inputKeys,
  citations,
  hasDetail,
}: {
  check: CheckEntry;
  inputs: Record<string, unknown>;
  inputKeys: string[];
  citations: string[];
  hasDetail: boolean;
}) {
  return (
    <div className="flex flex-col gap-3 text-xs">
      {/* verdict framing + meta */}
      <dl className="grid gap-3 sm:grid-cols-3">
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
            Outcome
          </dt>
          <dd className="mt-1 text-sm text-ink">{verdictFraming(check.verdict)}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
            Severity
          </dt>
          <dd className="mt-1 font-mono text-sm text-ink">{check.severity}</dd>
        </div>
        {check.duration_ms != null ? (
          <div>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
              Evaluated in
            </dt>
            <dd className="mt-1 font-mono text-sm text-ink">
              {check.duration_ms.toFixed(1)} ms
            </dd>
          </div>
        ) : null}
      </dl>

      {inputKeys.length > 0 ? (
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
            Inputs the check used
          </p>
          <dl className="overflow-hidden rounded-md border border-border">
            {inputKeys.map((k) => (
              <div
                key={k}
                className="grid grid-cols-1 gap-1 border-b border-border px-3 py-2 last:border-b-0 sm:grid-cols-[minmax(9rem,14rem)_minmax(0,1fr)] sm:items-baseline sm:gap-4"
              >
                <dt className="text-sm text-ink-subtle">{humaniseKey(k)}</dt>
                <dd className="break-words font-mono text-sm text-ink">
                  {formatInputValue(inputs[k])}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : (
        !hasDetail && (
          <p className="text-ink-subtle">
            This check reached its verdict from the invoice fields shown above; it
            recorded no additional intermediate inputs.
          </p>
        )
      )}

      {/* cited clauses — the authority */}
      {citations.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
            Cited clauses
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {citations.map((c) => (
              <li
                key={c}
                className="rounded bg-brand-soft px-1.5 py-0.5 font-mono text-[11px] text-brand-ink"
              >
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
