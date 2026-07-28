/**
 * App-vs-TradeDrawer event ownership for operation_failed events.
 *
 * A trade `operation_failed` reaches BOTH the App's `onAppEvent` handler and
 * the TradeDrawer's own subscription. The TradeDrawer owns trade operations
 * (it tracks their `operation_id` and shows the correct create/update/delete
 * retry). The App's generic failure path must NOT also surface it as a global
 * `backgroundError`, because the top banner's "重试" calls `retryLive` /
 * `retryService` - the wrong action for a trade command.
 *
 * `affected_capability` is a frozen `application_error` enum value
 * (`app-v1.schema.json`), so routing on `"trades"` is contract-safe and mirrors
 * the existing `"preferences"` filtering in `retryLiveOrService`.
 */

/**
 * True when an `application_error` is trade-scoped and therefore owned by the
 * TradeDrawer, not the App's generic failure path. Accepts the raw error
 * object or an envelope wrapping one.
 */
export function isTradeScopedError(error) {
  const candidate = error?.error ?? error?.payload ?? error;
  return (
    candidate !== null &&
    typeof candidate === "object" &&
    candidate?.affected_capability === "trades"
  );
}
