/** Notify the Operations page to reload when runs change elsewhere in the app. */

export const OPERATIONS_REFRESH_EVENT = "ap-operations-refresh";

export function notifyOperationsRefresh(): void {
  window.dispatchEvent(new CustomEvent(OPERATIONS_REFRESH_EVENT));
}
