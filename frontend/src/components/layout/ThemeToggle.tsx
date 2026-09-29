import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import { initialTheme, setTheme, type Theme } from "@/lib/theme";

/**
 * Dark/light toggle. Persistence + application live in lib/theme; this owns the
 * button and the reactive state. Defaults to dark — the control-room default —
 * and respects a stored preference across reloads.
 */
export function ThemeToggle() {
  const [theme, setThemeState] = useState<Theme>(initialTheme);

  useEffect(() => {
    setTheme(theme);
  }, [theme]);

  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={() => setThemeState(isDark ? "light" : "dark")}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      title={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-md text-ink-subtle",
        "hover:bg-surface-2 hover:text-ink transition-colors",
      )}
    >
      {isDark ? (
        <svg
          viewBox="0 0 24 24"
          width="1.15em"
          height="1.15em"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M20 14.5A8 8 0 1 1 9.5 4 6.5 6.5 0 0 0 20 14.5Z" />
        </svg>
      ) : (
        <svg
          viewBox="0 0 24 24"
          width="1.15em"
          height="1.15em"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" />
        </svg>
      )}
    </button>
  );
}
