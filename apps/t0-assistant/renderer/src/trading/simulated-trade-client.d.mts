import type { TradeBridge, TradeClient } from "./trade-client.mjs";

export function createSimulatedTradeClient(
  bridge: TradeBridge,
  sessionId: string,
): TradeClient;
