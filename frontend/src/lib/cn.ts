import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge class names, resolving Tailwind conflicts (later wins). The standard
 * shadcn/ui helper — used by every component so conditional classes compose
 * without duplicate-utility bugs.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
