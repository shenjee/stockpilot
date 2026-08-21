export function waitForLiveProjectionReady(args: {
  getProjection: () =>
    | {
        serviceGeneration?: number | null;
        sessionId?: string | null;
        snapshot?: { session?: { symbol?: string; state?: string } };
      }
    | null
    | undefined;
  subscribe: (listener: (projection: unknown) => void) => () => void;
  symbol: string;
  sessionId?: string | null;
  serviceGeneration?: number | null;
  isCurrent: () => boolean;
  timeoutMs?: number;
}): Promise<boolean>;
