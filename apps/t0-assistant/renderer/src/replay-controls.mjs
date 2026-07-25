export const REPLAY_SPEEDS = Object.freeze([1, 2, 5, 10]);

const ACTIVE_STATES = new Set(["ready", "playing", "paused"]);

export function replayFactsFromSnapshot(snapshot) {
  const session = snapshot?.session;
  const replay = snapshot?.replay;
  if (
    !session ||
    session.session_type !== "replay" ||
    typeof session.session_id !== "string" ||
    !ACTIVE_STATES.has(session.state) ||
    !replay ||
    !["one_minute", "five_minute"].includes(replay.granularity) ||
    !REPLAY_SPEEDS.includes(replay.playback_speed) ||
    typeof replay.current_time !== "string" ||
    typeof replay.start_time !== "string" ||
    typeof replay.end_time !== "string" ||
    (replay.next_bar_time !== null &&
      typeof replay.next_bar_time !== "string")
  ) {
    return null;
  }
  const start = marketTimeValue(replay.start_time);
  const current = marketTimeValue(replay.current_time);
  const end = marketTimeValue(replay.end_time);
  if (start === null || current === null || end === null || start > end) {
    return null;
  }
  return Object.freeze({
    sessionId: session.session_id,
    state: session.state,
    granularity: replay.granularity,
    currentTime: replay.current_time,
    nextBarTime: replay.next_bar_time,
    startTime: replay.start_time,
    endTime: replay.end_time,
    playbackSpeed: replay.playback_speed,
    startValue: start,
    currentValue: Math.min(end, Math.max(start, current)),
    endValue: end,
  });
}

export function deriveReplayControls(facts, { busy = false } = {}) {
  if (!facts) {
    return Object.freeze({
      active: false,
      playing: false,
      canTogglePlayback: false,
      canSeek: false,
      canStep: false,
      canChangeSpeed: false,
      stepLabel: "前进 1 分钟",
      granularityLabel: "",
    });
  }
  const interactive = ACTIVE_STATES.has(facts.state) && !busy;
  return Object.freeze({
    active: true,
    playing: facts.state === "playing",
    canTogglePlayback: interactive,
    canSeek: interactive,
    canStep: interactive && facts.nextBarTime !== null,
    canChangeSpeed: interactive,
    stepLabel:
      facts.granularity === "five_minute"
        ? "前进 5 分钟"
        : "前进 1 分钟",
    granularityLabel:
      facts.granularity === "five_minute" ? "5 分钟回放" : "1 分钟回放",
  });
}

export function replaySessionMatches(activeSessionId, candidateSessionId) {
  return (
    typeof activeSessionId === "string" &&
    activeSessionId.length > 0 &&
    candidateSessionId === activeSessionId
  );
}

export function replayOperationMatches(activeOperationId, candidateOperationId) {
  return (
    typeof activeOperationId === "string" &&
    activeOperationId.length > 0 &&
    candidateOperationId === activeOperationId
  );
}

export function marketTimeValue(timestamp) {
  const match =
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/.exec(
      timestamp,
    );
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match.map(Number);
  return Date.UTC(year, month - 1, day, hour, minute, second);
}

export function marketTimeFromValue(value) {
  const date = new Date(Number(value));
  if (!Number.isFinite(date.getTime())) {
    throw new TypeError("Replay progress value must be a finite timestamp");
  }
  return [
    `${date.getUTCFullYear()}-${twoDigits(date.getUTCMonth() + 1)}-${twoDigits(
      date.getUTCDate(),
    )}`,
    `${twoDigits(date.getUTCHours())}:${twoDigits(
      date.getUTCMinutes(),
    )}:${twoDigits(date.getUTCSeconds())}`,
  ].join(" ");
}

export function marketClockLabel(timestamp) {
  const value = marketTimeValue(timestamp);
  if (value === null) return "--:--";
  const date = new Date(value);
  return `${twoDigits(date.getUTCHours())}:${twoDigits(date.getUTCMinutes())}`;
}

function twoDigits(value) {
  return String(value).padStart(2, "0");
}
