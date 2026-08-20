/**
 * Replay session + projection owner (#155 PR2).
 *
 * Owns the authoritative Replay ChartProjection, the frozen
 * `loadingFallbackProjection` captured at Replay mode entry, session /
 * operation matching, cursor race tracking, and Replay UI lifecycle flags
 * (loading / busy / playbackPending / resume-after-seek).
 *
 * Does not own form inputs such as `replayDate`. React-free: subscribe().
 */

import {
  captureLoadingFallbackProjection,
  hasAuthoritativeReplayProjection,
} from "./active-workbench-projection.mjs";
import {
  applyWorkbenchSnapshot,
  createChartProjection,
} from "./chart-projection.mjs";
import { createReplayCursorTracker } from "../replay-cursor-tracker.mjs";
import {
  REPLAY_SPEEDS,
  replayFactsFromSnapshot,
  replayOperationMatches,
  replaySessionMatches,
} from "../replay-controls.mjs";

/**
 * @typedef {import("./chart-projection.mjs").ChartProjection} ChartProjection
 * @typedef {import("./chart-model.mjs").WorkbenchChartSnapshot} WorkbenchChartSnapshot
 */

export class ReplaySessionController {
  constructor() {
    /** @type {ChartProjection | null} */
    this._projection = null;
    /** @type {ChartProjection | null} */
    this._loadingFallbackProjection = null;
    /** @type {boolean} */
    this._inReplayMode = false;
    /** @type {string | null} */
    this._sessionId = null;
    /** @type {string | null} */
    this._loadOperationId = null;
    /** @type {number | null} */
    this._serviceGeneration = null;
    /** @type {boolean} */
    this._loading = false;
    /** @type {boolean} */
    this._busy = false;
    /** @type {boolean} */
    this._playbackPending = false;
    /** @type {boolean} */
    this._resumeAfterSeek = false;
    this._cursorTracker = createReplayCursorTracker();
    /** @type {Set<() => void>} */
    this._listeners = new Set();
  }

  /** @returns {ChartProjection | null} */
  get projection() {
    return this._projection;
  }

  /** @returns {ChartProjection | null} */
  get loadingFallbackProjection() {
    return this._loadingFallbackProjection;
  }

  /** @returns {boolean} */
  get inReplayMode() {
    return this._inReplayMode;
  }

  /** @returns {string | null} */
  get sessionId() {
    return this._sessionId;
  }

  /** @returns {string | null} */
  get loadOperationId() {
    return this._loadOperationId;
  }

  /** @returns {boolean} */
  get loading() {
    return this._loading;
  }

  /** @returns {boolean} */
  get busy() {
    return this._busy;
  }

  /** @returns {boolean} */
  get playbackPending() {
    return this._playbackPending;
  }

  /** @returns {boolean} */
  get resumeAfterSeek() {
    return this._resumeAfterSeek;
  }

  /** @returns {boolean} */
  get hasAuthoritativeProjection() {
    return hasAuthoritativeReplayProjection(this._projection);
  }

  /**
   * Enter Replay mode and freeze the current foreground projection as the
   * loading fallback. Must run at mode switch — not at beginReplay.
   * @param {ChartProjection | null | undefined} foregroundProjection
   */
  enterMode(foregroundProjection) {
    this._loadingFallbackProjection = captureLoadingFallbackProjection(
      foregroundProjection,
    );
    this._inReplayMode = true;
    this._notify();
  }

  /**
   * Leave Replay mode and clear session / fallback / lifecycle flags.
   * @returns {string | null} previous session id (for endReplay)
   */
  exitMode() {
    const previousSessionId = this._sessionId;
    this._inReplayMode = false;
    this._projection = null;
    this._loadingFallbackProjection = null;
    this._sessionId = null;
    this._loadOperationId = null;
    this._loading = false;
    this._busy = false;
    this._playbackPending = false;
    this._resumeAfterSeek = false;
    this._cursorTracker.clear();
    this._notify();
    return previousSessionId;
  }

  /**
   * Clear Replay authority when service generation changes.
   * Old-generation fallback must not remain displayable.
   */
  clearForGenerationChange() {
    this._projection = null;
    this._loadingFallbackProjection = null;
    this._sessionId = null;
    this._loadOperationId = null;
    this._loading = false;
    this._busy = false;
    this._playbackPending = false;
    this._resumeAfterSeek = false;
    this._cursorTracker.clear();
    this._notify();
  }

  /**
   * Bind the controller to the current service generation used for gates.
   * @param {number | null} generation
   */
  setServiceGeneration(generation) {
    this._serviceGeneration = Number.isInteger(generation) ? generation : null;
  }

  /**
   * Start (or restart) a Replay session. Prior successful projection becomes
   * the next loading fallback; authoritative Replay is cleared until the
   * first matching snapshot arrives.
   * @param {string} sessionId
   * @param {string | null} [loadOperationId]
   */
  beginSession(sessionId, loadOperationId = null) {
    if (!this._inReplayMode) {
      throw new Error("beginSession requires Replay mode");
    }
    if (typeof sessionId !== "string" || sessionId.length === 0) {
      throw new TypeError("beginSession requires a non-empty sessionId");
    }
    if (this._projection != null) {
      this._loadingFallbackProjection = captureLoadingFallbackProjection(
        this._projection,
      );
      this._projection = null;
    }
    this._sessionId = sessionId;
    this._loadOperationId =
      typeof loadOperationId === "string" && loadOperationId.length > 0
        ? loadOperationId
        : null;
    this._loading = true;
    this._busy = false;
    this._playbackPending = false;
    this._resumeAfterSeek = false;
    this._cursorTracker.clear();
    if (!this._loadOperationId) {
      this._loading = false;
    }
    this._notify();
  }

  /**
   * Accept a Replay workbench snapshot when generation/session/facts match.
   * Outer envelope identity is required: do not fill revision/session/
   * generation from the snapshot payload. Payload `session.revision` must
   * equal the outer revision. While a load operation is outstanding,
   * `identity.operation_id` must also match before the first authoritative
   * projection can replace fallback (#155: generation/session/operation
   * gate is atomic).
   * @param {WorkbenchChartSnapshot} snapshot
   * @param {{
   *   service_generation?: number,
   *   session_id?: string,
   *   revision?: number,
   *   operation_id?: string | null,
   * }} [identity]
   * @returns {boolean}
   */
  acceptSnapshot(snapshot, identity = {}) {
    if (!this._inReplayMode || this._sessionId == null) return false;
    // Outer envelope identity is authoritative — never fill revision /
    // session / generation from the snapshot payload (#162 review P2).
    const sessionId = identity.session_id;
    const generation = identity.service_generation;
    const revision = identity.revision;
    if (
      generation !== this._serviceGeneration ||
      !replaySessionMatches(this._sessionId, sessionId) ||
      !Number.isInteger(revision) ||
      snapshot.session?.revision !== revision
    ) {
      return false;
    }
    // Initial load: operation must match inside the same accept boundary so a
    // same-session advancing snapshot cannot replace fallback while loading
    // stays true (#155 review follow-up).
    if (
      this._loadOperationId != null &&
      !replayOperationMatches(this._loadOperationId, identity.operation_id)
    ) {
      return false;
    }
    const facts = replayFactsFromSnapshot(snapshot);
    if (!facts || facts.sessionId !== this._sessionId) {
      return false;
    }
    if (this._projection == null) {
      this._projection = createChartProjection(snapshot, {
        service_generation: generation,
        session_id: sessionId,
        revision,
      });
      if (this._loadOperationId != null) {
        this._loadOperationId = null;
        this._loading = false;
      }
      this._notify();
      return true;
    }
    const next = applyWorkbenchSnapshot(this._projection, snapshot, {
      service_generation: generation,
      session_id: sessionId,
      revision,
    });
    if (next === this._projection) return false;
    this._projection = next;
    this._notify();
    return true;
  }

  /**
   * Patch session_status fields onto the authoritative Replay snapshot.
   * Requires an integer revision strictly greater than the current projection
   * revision; missing / float / NaN revisions are rejected (#155 review).
   * @param {{ state?: unknown, playback_speed?: unknown, revision?: number | null }} payload
   * @returns {boolean}
   */
  applySessionStatus(payload) {
    const current = this._projection;
    if (!current?.snapshot?.session || !current.snapshot.replay) {
      return false;
    }
    if (!Number.isInteger(payload.revision)) {
      return false;
    }
    const nextRevision = payload.revision;
    // Same-session revision must strictly advance; stale/equal status must not
    // roll back or mutate projection identity without advancing revision.
    if (
      Number.isInteger(current.revision) &&
      nextRevision <= current.revision
    ) {
      return false;
    }
    const nextState =
      typeof payload.state === "string"
        ? payload.state
        : current.snapshot.session.state;
    const nextSpeed = REPLAY_SPEEDS.includes(
      /** @type {1 | 2 | 5 | 10} */ (payload.playback_speed),
    )
      ? /** @type {1 | 2 | 5 | 10} */ (payload.playback_speed)
      : current.snapshot.replay.playback_speed;
    this._projection = {
      ...current,
      revision: nextRevision,
      snapshot: {
        ...current.snapshot,
        session: {
          ...current.snapshot.session,
          state: nextState,
          revision: nextRevision,
        },
        replay: {
          ...current.snapshot.replay,
          playing: nextState === "playing",
          playback_speed: nextSpeed,
        },
      },
    };
    this._notify();
    return true;
  }

  /**
   * @param {string | null | undefined} operationId
   * @returns {boolean}
   */
  matchesLoadOperation(operationId) {
    return replayOperationMatches(this._loadOperationId, operationId);
  }

  clearLoadOperation() {
    if (this._loadOperationId == null && this._loading === false) return;
    this._loadOperationId = null;
    this._loading = false;
    this._notify();
  }

  /**
   * Load-operation failure: drop session authority and settle UI flags.
   */
  failLoadOperation() {
    this._loadOperationId = null;
    this._sessionId = null;
    this._projection = null;
    this._loading = false;
    this._busy = false;
    this._resumeAfterSeek = false;
    this._cursorTracker.clear();
    this._notify();
  }

  setLoading(value) {
    const next = value === true;
    if (this._loading === next) return;
    this._loading = next;
    this._notify();
  }

  setBusy(value) {
    const next = value === true;
    if (this._busy === next) return;
    this._busy = next;
    this._notify();
  }

  setPlaybackPending(value) {
    const next = value === true;
    if (this._playbackPending === next) return;
    this._playbackPending = next;
    this._notify();
  }

  setResumeAfterSeek(value) {
    this._resumeAfterSeek = value === true;
  }

  /**
   * @param {string | null | undefined} operationId
   * @returns {{ status: string, early: string | null }}
   */
  adoptCursorOperation(operationId) {
    const result = this._cursorTracker.adopt(operationId);
    if (
      result.status === "no_operation" ||
      result.status === "already_settled"
    ) {
      this._busy = false;
      if (result.early === "completed" && this._resumeAfterSeek) {
        // Caller triggers playback; flag is consumed by takeResumeAfterSeek.
      } else if (result.status !== "tracking") {
        this._resumeAfterSeek = false;
      }
      this._notify();
    }
    return result;
  }

  /**
   * @param {string | null | undefined} operationId
   * @param {"completed" | "failed"} kind
   * @returns {"settled" | "cached" | "ignored"}
   */
  noteCursorOutcome(operationId, kind) {
    const note = this._cursorTracker.noteOutcome(operationId, kind);
    if (note === "settled") {
      this._busy = false;
      if (kind === "failed") {
        this._resumeAfterSeek = false;
      }
      this._notify();
    }
    return note;
  }

  clearCursor() {
    this._cursorTracker.clear();
  }

  /**
   * Consume the resume-after-seek flag if set.
   * @returns {boolean}
   */
  takeResumeAfterSeek() {
    if (!this._resumeAfterSeek) return false;
    this._resumeAfterSeek = false;
    return true;
  }

  /**
   * @param {() => void} listener
   * @returns {() => void}
   */
  subscribe(listener) {
    if (typeof listener !== "function") {
      throw new TypeError("subscribe requires a listener function");
    }
    this._listeners.add(listener);
    return () => this._listeners.delete(listener);
  }

  _notify() {
    for (const listener of this._listeners) {
      try {
        listener();
      } catch {
        // Listener errors must not break session ownership.
      }
    }
  }
}
