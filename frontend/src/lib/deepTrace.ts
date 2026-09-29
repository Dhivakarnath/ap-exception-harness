/** Shared deep-trace preference (Operations toggle + upload/trigger). */

export const DEEP_TRACE_STORAGE = "ap-deep-trace-preference";

export function getDeepTracePreference(): boolean {
  try {
    return localStorage.getItem(DEEP_TRACE_STORAGE) === "1";
  } catch {
    return false;
  }
}

export function setDeepTracePreferenceLocal(enabled: boolean): void {
  try {
    localStorage.setItem(DEEP_TRACE_STORAGE, enabled ? "1" : "0");
  } catch {
    /* private mode */
  }
}
