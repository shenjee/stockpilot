export function enterTodayChart(args: {
  symbol: string;
  beginNavigation: () => number;
  isCurrent: (sequence: number) => boolean;
  resolveSecurity: (symbol: string) => Promise<object | null>;
  performSecuritySelection: (
    identity: object,
    restoring: boolean,
    options: { navigationSequence: number },
  ) => Promise<boolean>;
  isReplayMode: () => boolean;
  selectLiveMode: () => void;
}): Promise<
  | { ok: true }
  | { ok: false; reason: "identity" | "stale" | "live_not_ready" }
>;
