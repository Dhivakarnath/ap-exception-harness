import type { SVGProps } from "react";

/**
 * A tiny, dependency-free icon set — 24px grid, 1.7 stroke, round caps — sized
 * by the parent via `className` (default 1.15rem). Kept inline rather than
 * pulling an icon library so the bundle stays lean and every glyph matches the
 * same optical weight as the rest of the UI.
 */
type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="1.15em"
      height="1.15em"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

/** Runs — a stack of invoices flowing through. */
export function RunsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 5.5h11l4 4v9a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5v-11A1.5 1.5 0 0 1 4 5.5Z" />
      <path d="M15 5.5v4h4" />
      <path d="M6.5 13.5l2 2 4-4" />
    </Icon>
  );
}

/** Policy — a rulebook / document with lines. */
export function PolicyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3.5h9l4 4V19a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 6 3.5Z" />
      <path d="M15 3.5v4h4" />
      <path d="M8 12h8M8 15.5h5" />
    </Icon>
  );
}

/** Reviews — person with checkmark (HITL queue). */
export function ReviewsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20c0-3.5 3.1-6 7-6s7 2.5 7 6" />
      <path d="M16.5 11.5l1.5 1.5 3-3" />
    </Icon>
  );
}

/** Guardrails — a shield. */
export function GuardrailsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3.5l7 2.5v5c0 4.2-2.9 7.4-7 9-4.1-1.6-7-4.8-7-9v-5l7-2.5Z" />
      <path d="M9 12l2 2 4-4.5" />
    </Icon>
  );
}

/** Observability — an activity pulse. */
export function ObservabilityIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 12h4l2.5-6 5 12 2.5-6H21" />
    </Icon>
  );
}

/** Evals — a checklist / scorecard. */
export function EvalsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9 4.5h6M7.5 4.5H6A1.5 1.5 0 0 0 4.5 6v13A1.5 1.5 0 0 0 6 20.5h12A1.5 1.5 0 0 0 19.5 19V6A1.5 1.5 0 0 0 18 4.5h-1.5" />
      <path d="M9 3.5h6v2H9z" />
      <path d="M8 11l1.5 1.5L12 10M8 16l1.5 1.5L12 15" />
      <path d="M14.5 11H17M14.5 16H17" />
    </Icon>
  );
}

/** Back / chevron-left. */
export function BackIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M14 6l-6 6 6 6" />
    </Icon>
  );
}

/** External link. */
export function ExternalIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10" />
    </Icon>
  );
}

/** Chevron for expandable rows. */
export function ChevronIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9 6l6 6-6 6" />
    </Icon>
  );
}

/** Chevron down for selects and menus. */
export function ChevronDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 9l6 6 6-6" />
    </Icon>
  );
}

/** Tenant / organization — used for upload-target selection. */
export function TenantIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4.5 20V9.5L12 5l7.5 4.5V20" />
      <path d="M9 20v-5.5h6V20" />
      <path d="M4.5 9.5 12 5l7.5 4.5" />
    </Icon>
  );
}
