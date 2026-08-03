declare global {
  type ServiceState =
    | "starting"
    | "ready"
    | "restarting"
    | "connected"
    | "disconnected"
    | "stopping"
    | "stopped"
    | "failed";

  interface ServiceStatus {
    state: ServiceState;
    service_generation: number;
    message: string;
  }

  interface StockPilotBridge {
    getServiceStatus(): Promise<ServiceStatus>;
    retryService(): Promise<ServiceStatus>;
    searchSecurities(request: unknown): Promise<unknown>;
    onServiceStatus(listener: (status: ServiceStatus) => void): () => void;
    selectSecurity(request: unknown): Promise<unknown>;
    saveLastSymbol(request: unknown): Promise<unknown>;
    getLiveSnapshot(request: unknown): Promise<unknown>;
    retryLive(request: unknown): Promise<unknown>;
    listTrades(request: unknown): Promise<unknown>;
    createTrade(request: unknown): Promise<unknown>;
    updateTrade(request: unknown): Promise<unknown>;
    deleteTrade(request: unknown): Promise<unknown>;
    listFeePlans(request: unknown): Promise<unknown>;
    createFeePlan(request: unknown): Promise<unknown>;
    updateFeePlan(request: unknown): Promise<unknown>;
    deleteFeePlan(request: unknown): Promise<unknown>;
    calculateTradeFee(request: unknown): Promise<unknown>;
    getPreferences(request: unknown): Promise<unknown>;
    savePreferences(request: unknown): Promise<unknown>;
    getHistoricalSnapshot(request: unknown): Promise<unknown>;
    selectSymbol(request: unknown): Promise<unknown>;
    beginReplay(request: unknown): Promise<unknown>;
    setReplayPlayback(request: unknown): Promise<unknown>;
    setReplaySpeed(request: unknown): Promise<unknown>;
    stepReplay(request: unknown): Promise<unknown>;
    seekReplay(request: unknown): Promise<unknown>;
    endReplay(request: unknown): Promise<unknown>;
    getReplaySnapshot(request: unknown): Promise<unknown>;
    onAppEvent(listener: (event: unknown) => void): () => void;
    onReplayEvent(listener: (event: unknown) => void): () => void;
    onReplaySnapshot(listener: (snapshot: unknown) => void): () => void;
  }

  interface Window {
    stockpilot: StockPilotBridge;
  }
}

declare module "*.css";

export {};
