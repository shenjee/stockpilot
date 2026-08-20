/**
 * Parse a Replay stream envelope. Outer identity is authoritative: both
 * service_generation and revision must be integers. Callers must not fill
 * missing outer fields from payload (#155 / #162 review).
 *
 * @param {unknown} event
 * @returns {{
 *   event_type: string,
 *   operation_id: string | null,
 *   service_generation: number,
 *   session_id: string,
 *   revision: number,
 *   payload: unknown,
 * } | null}
 */
export function replayEventEnvelope(event) {
  if (!event || typeof event !== "object") return null;
  const envelope = /** @type {{
    schema_version?: unknown,
    event_type?: unknown,
    operation_id?: unknown,
    service_generation?: unknown,
    session_id?: unknown,
    revision?: unknown,
    payload?: unknown,
  }} */ (event);
  if (
    envelope.schema_version !== "t0_replay_v2" ||
    typeof envelope.event_type !== "string" ||
    typeof envelope.session_id !== "string" ||
    !Number.isInteger(envelope.service_generation) ||
    !Number.isInteger(envelope.revision)
  ) {
    return null;
  }
  return {
    event_type: envelope.event_type,
    operation_id:
      typeof envelope.operation_id === "string"
        ? envelope.operation_id
        : null,
    service_generation: envelope.service_generation,
    session_id: envelope.session_id,
    revision: envelope.revision,
    payload: envelope.payload,
  };
}
