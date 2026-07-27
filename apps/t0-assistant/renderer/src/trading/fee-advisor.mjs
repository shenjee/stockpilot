/**
 * Port through which the renderer obtains a *suggested* default fee for a
 * trade draft.
 *
 * The fee-calculation rule itself is owned by
 * `packages/t0assistant/trading/fee_policy.py` (`architecture.md` §5.6); it is
 * NOT reimplemented in the renderer. The renderer calls this port, and a
 * future backend fee-advisor command supplies the suggestion. Until that
 * command exists, `createNullFeeAdvisor` returns `null` (no suggestion) so the
 * fee field stays manual rather than fabricating a value. Tests inject a fake
 * advisor to exercise the form wiring.
 *
 * The suggestion is never authoritative: the user may override it, and the
 * persisted fee is never recomputed when a fee plan later changes.
 */

export function createNullFeeAdvisor() {
  return Object.freeze({
    suggestFee() {
      return null;
    },
  });
}

/**
 * Test/fixture advisor that delegates to an injected suggester. The suggester
 * must NOT reimplement the domain formula in production paths - tests use it
 * to return fixed values for deterministic assertions.
 */
export function createFakeFeeAdvisor(suggest) {
  if (typeof suggest !== "function") {
    throw new TypeError("createFakeFeeAdvisor requires a suggester function");
  }
  return Object.freeze({
    suggestFee(plan, input) {
      return suggest(plan, input);
    },
  });
}
