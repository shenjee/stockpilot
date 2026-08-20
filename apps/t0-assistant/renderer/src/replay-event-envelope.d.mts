export function replayEventEnvelope(event: unknown): {
  event_type: string;
  operation_id: string | null;
  service_generation: number;
  session_id: string;
  revision: number;
  payload: unknown;
} | null;
