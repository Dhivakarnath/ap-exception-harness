import type { CheckEntry } from "@/store/runState";
import type { Verdict } from "@/types/events";

/**
 * Presentation helpers for the deterministic checks. The backend already sends
 * everything needed to explain a verdict (threshold = the rule, actual = the
 * finding, reasoning = plain-language why, inputs = the raw values it used).
 * These helpers only shape that data for reading — they never invent it.
 */

/** Human labels + ordering for the check categories the engine emits. */
const CATEGORY_META: Record<string, { label: string; blurb: string; order: number }> = {
  document: {
    label: "Document integrity",
    blurb: "Is the invoice complete and internally consistent?",
    order: 0,
  },
  arithmetic: {
    label: "Arithmetic",
    blurb: "Do the numbers add up, in one currency?",
    order: 1,
  },
  duplicate: {
    label: "Duplicate detection",
    blurb: "Have we seen this bill — or a near-miss of it — before?",
    order: 2,
  },
  matching: {
    label: "Three-way match",
    blurb: "Does the invoice agree with its PO and goods receipt?",
    order: 3,
  },
  identity: {
    label: "Vendor identity",
    blurb: "Is the payee who they claim to be, paid where they always are?",
    order: 4,
  },
  fraud: {
    label: "Fraud signals",
    blurb: "Anything that smells like manipulation or a first-time payee?",
    order: 5,
  },
  authority: {
    label: "Authority & segregation",
    blurb: "Is this within the agent's authority, with duties separated?",
    order: 6,
  },
  integrity: {
    label: "Integrity",
    blurb: "General integrity checks.",
    order: 7,
  },
};

export function categoryMeta(category: string): {
  label: string;
  blurb: string;
  order: number;
} {
  return (
    CATEGORY_META[category] ?? { label: titleCase(category), blurb: "", order: 99 }
  );
}

export interface CheckGroup {
  category: string;
  label: string;
  blurb: string;
  checks: CheckEntry[];
  counts: Record<Verdict, number>;
}

/** Group checks by category, preserving evaluation order within a group. */
export function groupChecks(checks: CheckEntry[]): CheckGroup[] {
  const byCat = new Map<string, CheckEntry[]>();
  for (const c of checks) {
    const list = byCat.get(c.category) ?? [];
    list.push(c);
    byCat.set(c.category, list);
  }

  const groups: CheckGroup[] = [];
  for (const [category, list] of byCat) {
    const meta = categoryMeta(category);
    const counts: Record<Verdict, number> = { pass: 0, flag: 0, fail: 0, skip: 0 };
    for (const c of list) counts[c.verdict] += 1;
    groups.push({ category, label: meta.label, blurb: meta.blurb, checks: list, counts });
  }

  groups.sort((a, b) => categoryMeta(a.category).order - categoryMeta(b.category).order);
  return groups;
}

/** A one-line, plain-language framing of what the verdict means for this check. */
export function verdictFraming(verdict: Verdict): string {
  switch (verdict) {
    case "pass":
      return "Rule satisfied";
    case "flag":
      return "Rule triggered — raised for review";
    case "fail":
      return "Rule violated";
    case "skip":
      return "Not applicable — precondition not met";
  }
}

/** Turn a check's snake_case name into a readable title. */
export function checkTitle(name: string): string {
  return titleCase(name);
}

/** Humanise an inputs key ("printed_subtotal" -> "Printed subtotal"). */
export function humaniseKey(key: string): string {
  return titleCase(key);
}

/** Render an inputs value for display, honestly (arrays joined, objects JSON). */
export function formatInputValue(value: unknown): string {
  if (value == null) return "—";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function titleCase(s: string): string {
  const spaced = s.replace(/[_-]+/g, " ").trim();
  if (!spaced) return s;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
