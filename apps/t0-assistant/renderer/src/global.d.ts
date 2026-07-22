declare global {
  type ServiceState = "starting" | "ready" | "stopping" | "stopped" | "failed";

  interface ServiceStatus {
    state: ServiceState;
    service_generation: number;
    message: string;
  }

  interface StockPilotBridge {
    getServiceStatus(): Promise<ServiceStatus>;
    onServiceStatus(listener: (status: ServiceStatus) => void): () => void;
  }

  interface Window {
    stockpilot: StockPilotBridge;
  }
}

declare module "*.css";

export {};
