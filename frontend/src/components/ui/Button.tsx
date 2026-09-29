import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-brand text-brand-contrast hover:brightness-110 active:brightness-95",
  secondary:
    "bg-surface-3 text-ink hover:bg-surface-2 border border-border-strong",
  ghost: "text-ink-muted hover:bg-surface-2 hover:text-ink",
  danger: "bg-fail text-brand-contrast hover:brightness-110",
};

export function Button({
  variant = "primary",
  className,
  children,
  ...props
}: {
  variant?: Variant;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium",
        "transition-[filter,background-color,color] duration-150",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANT[variant],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
