"""App Coordinator lifecycle tests using only fake Session ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from threading import Event, Thread
import unittest

from packages.t0assistant.runtime import (
    AppCoordinator,
    AppMode,
    CoordinatorRetirementError,
    CoordinatorStateError,
    CoordinatorValidationError,
    SessionSpec,
    SessionType,
)


@dataclass
class _FakeSession:
    spec: SessionSpec
    retired: bool = False
    retire_count: int = 0
    activate_count: int = 0
    on_retire: Callable[[], None] | None = None
    on_activate: Callable[[], None] | None = None
    retire_failures_remaining: int = 0

    def activate(self) -> None:
        self.activate_count += 1
        if self.on_activate is not None:
            self.on_activate()

    def retire(self) -> None:
        self.retire_count += 1
        if self.on_retire is not None:
            self.on_retire()
        if self.retire_failures_remaining:
            self.retire_failures_remaining -= 1
            raise RuntimeError("fake retirement failed")
        self.retired = True


class _FakeSessionFactory:
    def __init__(self) -> None:
        self.created: list[_FakeSession] = []
        self.fail_next_live = False
        self.fail_next_replay = False
        self.on_create_live: Callable[[SessionSpec], None] | None = None
        self.on_create_replay: Callable[[SessionSpec], None] | None = None

    def create_live(self, spec: SessionSpec) -> _FakeSession:
        if self.fail_next_live:
            self.fail_next_live = False
            raise RuntimeError("fake live creation failed")
        if self.on_create_live is not None:
            self.on_create_live(spec)
        return self._add(spec, SessionType.LIVE)

    def create_replay(self, spec: SessionSpec) -> _FakeSession:
        if self.fail_next_replay:
            self.fail_next_replay = False
            raise RuntimeError("fake replay creation failed")
        if self.on_create_replay is not None:
            self.on_create_replay(spec)
        return self._add(spec, SessionType.REPLAY)

    def _add(
        self, spec: SessionSpec, expected: SessionType
    ) -> _FakeSession:
        if spec.session_type is not expected:
            raise AssertionError(f"expected {expected}, got {spec.session_type}")
        session = _FakeSession(spec)
        self.created.append(session)
        return session


def _session_id(session_type: SessionType, generation: int) -> str:
    return f"{session_type.value}-{generation}"


class AppCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = _FakeSessionFactory()
        self.coordinator = AppCoordinator(
            self.factory,
            session_id_factory=_session_id,
        )

    def test_starts_in_empty_live_mode(self) -> None:
        snapshot = self.coordinator.snapshot
        self.assertIsNone(snapshot.current_symbol)
        self.assertIs(snapshot.mode, AppMode.LIVE)
        self.assertEqual(snapshot.session_generation, 0)
        self.assertIsNone(snapshot.live_session)
        self.assertIsNone(snapshot.replay_session)
        self.assertIsNone(snapshot.visible_session)

    def test_select_symbol_creates_live_and_is_idempotent(self) -> None:
        first = self.coordinator.select_symbol("sh.600000")
        same = self.coordinator.select_symbol("sh.600000")

        self.assertEqual(first, same)
        self.assertEqual(len(self.factory.created), 1)
        live = first.live_session
        self.assertIsNotNone(live)
        assert live is not None
        self.assertEqual(live.session_id, "live-1")
        self.assertEqual(live.symbol, "sh.600000")
        self.assertEqual(live.generation, 1)
        self.assertIsNone(live.trade_date)
        self.assertEqual(first.visible_session, live)

    def test_replay_is_blank_until_begin_and_live_stays_active(self) -> None:
        selected = self.coordinator.select_symbol("sz.000001")
        live = selected.live_session
        assert live is not None

        blank = self.coordinator.set_mode("replay")
        self.assertIsNone(blank.replay_session)
        self.assertIsNone(blank.visible_session)
        self.assertTrue(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=live.session_id,
                generation=live.generation,
            )
        )
        self.assertFalse(self.factory.created[0].retired)

        started = self.coordinator.begin_replay(date(2026, 7, 23))
        replay = started.replay_session
        assert replay is not None
        self.assertEqual(replay.session_id, "replay-2")
        self.assertEqual(replay.symbol, "sz.000001")
        self.assertEqual(replay.trade_date, "2026-07-23")
        self.assertEqual(started.visible_session, replay)
        self.assertFalse(
            self.coordinator.is_visible_session(
                session_id=live.session_id,
                generation=live.generation,
            )
        )

    def test_selecting_from_empty_replay_creates_only_background_live(self) -> None:
        self.coordinator.set_mode("replay")

        selected = self.coordinator.select_symbol("sz.000001")

        self.assertEqual(selected.current_symbol, "sz.000001")
        self.assertIsNotNone(selected.live_session)
        self.assertIsNone(selected.replay_session)
        self.assertIsNone(selected.visible_session)

    def test_return_live_retires_replay_but_keeps_live(self) -> None:
        selected = self.coordinator.select_symbol("sh.600000")
        live = selected.live_session
        assert live is not None
        self.coordinator.set_mode(AppMode.REPLAY)
        started = self.coordinator.begin_replay("2026-07-22")
        replay = started.replay_session
        assert replay is not None

        result = self.coordinator.set_mode(AppMode.LIVE)

        self.assertTrue(self.factory.created[1].retired)
        self.assertEqual(self.factory.created[1].retire_count, 1)
        self.assertFalse(self.factory.created[0].retired)
        self.assertIsNone(result.replay_session)
        self.assertEqual(result.visible_session, live)
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="replay",
                session_id=replay.session_id,
                generation=replay.generation,
            )
        )

    def test_switch_symbol_retires_old_sessions_and_blanks_replay(self) -> None:
        first = self.coordinator.select_symbol("sh.600000")
        old_live = first.live_session
        assert old_live is not None
        self.coordinator.set_mode("replay")
        replay_snapshot = self.coordinator.begin_replay("2026-07-21")
        old_replay = replay_snapshot.replay_session
        assert old_replay is not None

        switched = self.coordinator.select_symbol("sz.000001")

        self.assertTrue(self.factory.created[0].retired)
        self.assertTrue(self.factory.created[1].retired)
        self.assertIs(switched.mode, AppMode.REPLAY)
        self.assertEqual(switched.current_symbol, "sz.000001")
        self.assertIsNone(switched.replay_session)
        self.assertIsNone(switched.visible_session)
        new_live = switched.live_session
        assert new_live is not None
        self.assertEqual(new_live.session_id, "live-3")
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=old_live.session_id,
                generation=old_live.generation,
            )
        )
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="replay",
                session_id=old_replay.session_id,
                generation=old_replay.generation,
            )
        )

    def test_rebuilds_consume_new_generations(self) -> None:
        self.coordinator.select_symbol("sh.600000")
        self.coordinator.set_mode("replay")
        self.coordinator.begin_replay("2026-07-21")

        second_replay = self.coordinator.begin_replay("2026-07-22")
        replay = second_replay.replay_session
        assert replay is not None
        self.assertTrue(self.factory.created[1].retired)
        self.assertEqual(replay.generation, 3)

        retried = self.coordinator.retry_live()
        live = retried.live_session
        assert live is not None
        self.assertTrue(self.factory.created[0].retired)
        self.assertEqual(live.generation, 4)
        self.assertEqual(retried.replay_session, replay)

    def test_result_requires_type_id_and_generation_match(self) -> None:
        snapshot = self.coordinator.select_symbol("sh.600000")
        live = snapshot.live_session
        assert live is not None

        for session_type, session_id, generation in (
            ("replay", live.session_id, live.generation),
            ("live", live.session_id, live.generation + 1),
            ("live", "other", live.generation),
        ):
            self.assertFalse(
                self.coordinator.accepts_result(
                    session_type=session_type,
                    session_id=session_id,
                    generation=generation,
                )
            )

    def test_session_is_invalidated_before_concrete_retirement_callback(self) -> None:
        snapshot = self.coordinator.select_symbol("sh.600000")
        live = snapshot.live_session
        assert live is not None
        accepted_during_retirement: list[bool] = []
        self.factory.created[0].on_retire = lambda: accepted_during_retirement.append(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=live.session_id,
                generation=live.generation,
            )
        )

        self.coordinator.retry_live()
        self.assertEqual(accepted_during_retirement, [False])

    def test_optional_activate_runs_after_session_enters_acceptance_boundary(self) -> None:
        seen: list[bool] = []
        original_create_live = self.factory.create_live

        def create_live(spec: SessionSpec) -> _FakeSession:
            session = original_create_live(spec)
            session.on_activate = lambda: seen.append(
                self.coordinator.accepts_result(
                    session_type="live",
                    session_id=spec.session_id,
                    generation=spec.generation,
                )
            )
            return session

        self.factory.create_live = create_live

        snapshot = self.coordinator.select_symbol("sh.600000")

        self.assertEqual(seen, [True])
        live = snapshot.live_session
        assert live is not None
        self.assertEqual(self.factory.created[0].activate_count, 1)

    def test_activate_may_call_accepts_result_without_deadlock(self) -> None:
        original_create_live = self.factory.create_live
        observed: list[bool] = []

        def create_live(spec: SessionSpec) -> _FakeSession:
            session = original_create_live(spec)

            def on_activate() -> None:
                completion = Event()

                def worker() -> None:
                    observed.append(
                        self.coordinator.accepts_result(
                            session_type="live",
                            session_id=spec.session_id,
                            generation=spec.generation,
                        )
                    )
                    completion.set()

                Thread(target=worker).start()
                if not completion.wait(timeout=0.5):
                    raise RuntimeError("deadlock detected")

            session.on_activate = on_activate
            return session

        self.factory.create_live = create_live

        snapshot = self.coordinator.select_symbol("sh.600000")
        self.assertIsNotNone(snapshot.live_session)
        self.assertEqual(observed, [True])

    def test_activation_failure_after_mode_change_removes_replacement_from_boundary(self) -> None:
        class _BlockingActivationFactory(_FakeSessionFactory):
            def __init__(self) -> None:
                super().__init__()
                self.started = Event()
                self.allow_fail = Event()

            def create_live(self, spec: SessionSpec) -> _FakeSession:
                session = super().create_live(spec)

                def on_activate() -> None:
                    self.started.set()
                    self.allow_fail.wait(timeout=1)
                    raise RuntimeError("fake activation failed")

                session.on_activate = on_activate
                return session

        factory = _BlockingActivationFactory()
        coordinator = AppCoordinator(factory, session_id_factory=_session_id)
        errors: list[Exception] = []
        mode_results = []

        def select_symbol() -> None:
            try:
                coordinator.select_symbol("sh.600000")
            except Exception as exc:
                errors.append(exc)

        def set_mode() -> None:
            mode_results.append(coordinator.set_mode("replay"))

        thread = Thread(target=select_symbol)
        thread.start()
        self.assertTrue(factory.started.wait(timeout=1))

        mode_thread = Thread(target=set_mode)
        mode_thread.start()
        factory.allow_fail.set()
        thread.join(timeout=1)
        mode_thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(mode_thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CoordinatorStateError)
        self.assertEqual(len(mode_results), 1)

        snapshot = coordinator.snapshot
        self.assertIs(snapshot.mode, AppMode.REPLAY)
        self.assertIsNone(snapshot.current_symbol)
        self.assertIsNone(snapshot.live_session)
        self.assertFalse(
            coordinator.accepts_result(
                session_type="live",
                session_id="live-1",
                generation=1,
            )
        )
        self.assertEqual(len(factory.created), 1)
        self.assertTrue(factory.created[0].retired)
        self.assertEqual(factory.created[0].retire_count, 1)

    def test_select_symbol_activation_failure_drops_derived_replay_for_new_symbol(self) -> None:
        initial = self.coordinator.select_symbol("sh.600000")
        old_live = initial.live_session
        assert old_live is not None
        self.coordinator.set_mode("replay")
        replay_snapshot = self.coordinator.begin_replay("2026-07-23")
        old_replay = replay_snapshot.replay_session
        assert old_replay is not None

        started = Event()
        allow_fail = Event()
        select_errors: list[Exception] = []
        replay_results = []
        replay_errors: list[Exception] = []

        original_create_live = self.factory.create_live

        def create_live(spec: SessionSpec) -> _FakeSession:
            session = original_create_live(spec)

            def on_activate() -> None:
                started.set()
                allow_fail.wait(timeout=1)
                raise RuntimeError("fake activation failed")

            session.on_activate = on_activate
            return session

        self.factory.create_live = create_live

        def select_symbol() -> None:
            try:
                self.coordinator.select_symbol("sz.000001")
            except Exception as exc:
                select_errors.append(exc)

        def begin_replay() -> None:
            try:
                replay_results.append(self.coordinator.begin_replay("2026-07-24"))
            except Exception as exc:
                replay_errors.append(exc)

        thread = Thread(target=select_symbol)
        thread.start()
        self.assertTrue(started.wait(timeout=1))

        replay_thread = Thread(target=begin_replay)
        replay_thread.start()

        allow_fail.set()
        replay_thread.join(timeout=1)
        thread.join(timeout=1)
        self.assertFalse(replay_thread.is_alive())
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(select_errors), 1)
        self.assertIsInstance(select_errors[0], CoordinatorStateError)
        self.assertEqual(replay_results, [])
        self.assertEqual(len(replay_errors), 1)
        self.assertIsInstance(replay_errors[0], CoordinatorStateError)

        snapshot = self.coordinator.snapshot
        self.assertEqual(snapshot.current_symbol, "sh.600000")
        self.assertEqual(snapshot.live_session, old_live)
        self.assertEqual(snapshot.replay_session, old_replay)
        self.assertIs(snapshot.mode, AppMode.REPLAY)
        assert snapshot.live_session is not None and snapshot.replay_session is not None
        self.assertEqual(snapshot.live_session.symbol, snapshot.current_symbol)
        self.assertEqual(snapshot.replay_session.symbol, snapshot.current_symbol)
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=self.factory.created[2].spec.session_id,
                generation=self.factory.created[2].spec.generation,
            )
        )
        self.assertTrue(self.factory.created[2].retired)
        self.assertEqual(self.factory.created[2].retire_count, 1)
        self.assertTrue(self.factory.created[3].retired)
        self.assertEqual(self.factory.created[3].retire_count, 1)
        self.assertFalse(self.factory.created[1].retired)

    def test_select_symbol_activation_failure_does_not_revive_replay_after_mode_live(self) -> None:
        self.coordinator.select_symbol("sh.600000")
        self.coordinator.set_mode("replay")
        replay_snapshot = self.coordinator.begin_replay("2026-07-23")
        old_replay = replay_snapshot.replay_session
        assert old_replay is not None

        started = Event()
        allow_fail = Event()
        select_errors: list[Exception] = []
        live_mode_results = []

        original_create_live = self.factory.create_live

        def create_live(spec: SessionSpec) -> _FakeSession:
            session = original_create_live(spec)

            def on_activate() -> None:
                started.set()
                allow_fail.wait(timeout=1)
                raise RuntimeError("fake activation failed")

            session.on_activate = on_activate
            return session

        self.factory.create_live = create_live

        def select_symbol() -> None:
            try:
                self.coordinator.select_symbol("sz.000001")
            except Exception as exc:
                select_errors.append(exc)

        def set_live_mode() -> None:
            live_mode_results.append(self.coordinator.set_mode("live"))

        thread = Thread(target=select_symbol)
        thread.start()
        self.assertTrue(started.wait(timeout=1))

        mode_thread = Thread(target=set_live_mode)
        mode_thread.start()

        allow_fail.set()
        mode_thread.join(timeout=1)
        thread.join(timeout=1)
        self.assertFalse(mode_thread.is_alive())
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(live_mode_results), 1)
        live_mode_snapshot = live_mode_results[0]
        self.assertIs(live_mode_snapshot.mode, AppMode.LIVE)
        self.assertIsNone(live_mode_snapshot.replay_session)
        self.assertEqual(len(select_errors), 1)
        self.assertIsInstance(select_errors[0], CoordinatorStateError)

        snapshot = self.coordinator.snapshot
        self.assertIs(snapshot.mode, AppMode.LIVE)
        self.assertEqual(snapshot.current_symbol, "sh.600000")
        self.assertIsNotNone(snapshot.live_session)
        self.assertIsNone(snapshot.replay_session)
        assert snapshot.live_session is not None
        self.assertEqual(snapshot.live_session.symbol, snapshot.current_symbol)
        self.assertTrue(self.factory.created[1].retired)
        self.assertEqual(self.factory.created[1].retire_count, 1)
        self.assertTrue(self.factory.created[2].retired)
        self.assertEqual(self.factory.created[2].retire_count, 1)

    def test_overlapping_select_symbol_failures_do_not_restore_failed_predecessor(self) -> None:
        initial = self.coordinator.select_symbol("sh.600000")
        stable_live = initial.live_session
        assert stable_live is not None

        started_b = Event()
        allow_fail_b = Event()
        started_c = Event()
        allow_fail_c = Event()
        done_c = Event()
        select_errors: list[Exception] = []

        original_create_live = self.factory.create_live

        def create_live(spec: SessionSpec) -> _FakeSession:
            session = original_create_live(spec)
            if spec.symbol == "sz.000001":
                def fail_b() -> None:
                    started_b.set()
                    allow_fail_b.wait(timeout=1)
                    raise RuntimeError("B activation failed")

                session.on_activate = fail_b
            elif spec.symbol == "sz.000002":
                def fail_c() -> None:
                    started_c.set()
                    allow_fail_c.wait(timeout=1)
                    raise RuntimeError("C activation failed")

                session.on_activate = fail_c
            return session

        self.factory.create_live = create_live

        def select_symbol(symbol: str) -> None:
            try:
                self.coordinator.select_symbol(symbol)
            except Exception as exc:
                select_errors.append(exc)
            finally:
                if symbol == "sz.000002":
                    done_c.set()

        thread_b = Thread(target=lambda: select_symbol("sz.000001"))
        thread_b.start()
        self.assertTrue(started_b.wait(timeout=1))

        thread_c = Thread(target=lambda: select_symbol("sz.000002"))
        thread_c.start()
        self.assertFalse(done_c.wait(timeout=0.1))

        allow_fail_b.set()
        self.assertTrue(started_c.wait(timeout=1))
        allow_fail_c.set()

        thread_b.join(timeout=1)
        thread_c.join(timeout=1)
        self.assertFalse(thread_b.is_alive())
        self.assertFalse(thread_c.is_alive())
        self.assertEqual(len(select_errors), 2)
        self.assertTrue(
            all(isinstance(error, CoordinatorStateError) for error in select_errors)
        )

        snapshot = self.coordinator.snapshot
        self.assertEqual(snapshot.current_symbol, "sh.600000")
        self.assertEqual(snapshot.live_session, stable_live)
        self.assertIsNone(snapshot.replay_session)
        self.assertIs(snapshot.mode, AppMode.LIVE)
        assert snapshot.live_session is not None
        self.assertEqual(snapshot.live_session.symbol, snapshot.current_symbol)
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=self.factory.created[1].spec.session_id,
                generation=self.factory.created[1].spec.generation,
            )
        )
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=self.factory.created[2].spec.session_id,
                generation=self.factory.created[2].spec.generation,
            )
        )
        self.assertTrue(self.factory.created[1].retired)
        self.assertEqual(self.factory.created[1].retire_count, 1)
        self.assertTrue(self.factory.created[2].retired)
        self.assertEqual(self.factory.created[2].retire_count, 1)
        self.assertFalse(self.factory.created[0].retired)

    def test_select_symbol_idempotent_call_does_not_bypass_inflight_activation(self) -> None:
        initial = self.coordinator.select_symbol("sh.600000")
        stable_live = initial.live_session
        assert stable_live is not None

        started = Event()
        allow_fail = Event()
        first_errors: list[Exception] = []
        second_errors: list[Exception] = []
        second_done = Event()

        original_create_live = self.factory.create_live

        def create_live(spec: SessionSpec) -> _FakeSession:
            session = original_create_live(spec)
            if spec.symbol == "sz.000001" and spec.generation == 2:
                def fail_after_block() -> None:
                    started.set()
                    allow_fail.wait(timeout=1)
                    raise RuntimeError("activation failed")

                session.on_activate = fail_after_block
            elif spec.symbol == "sz.000001":
                session.on_activate = lambda: (_ for _ in ()).throw(
                    RuntimeError("activation failed")
                )
            return session

        self.factory.create_live = create_live

        def first() -> None:
            try:
                self.coordinator.select_symbol("sz.000001")
            except Exception as exc:
                first_errors.append(exc)

        def second() -> None:
            try:
                self.coordinator.select_symbol("sz.000001")
            except Exception as exc:
                second_errors.append(exc)
            finally:
                second_done.set()

        thread_first = Thread(target=first)
        thread_first.start()
        self.assertTrue(started.wait(timeout=1))

        thread_second = Thread(target=second)
        thread_second.start()
        self.assertFalse(second_done.wait(timeout=0.1))

        allow_fail.set()
        thread_first.join(timeout=1)
        thread_second.join(timeout=1)
        self.assertFalse(thread_first.is_alive())
        self.assertFalse(thread_second.is_alive())
        self.assertEqual(len(first_errors), 1)
        self.assertIsInstance(first_errors[0], CoordinatorStateError)
        self.assertEqual(len(second_errors), 1)
        self.assertIsInstance(second_errors[0], CoordinatorStateError)

        snapshot = self.coordinator.snapshot
        self.assertEqual(snapshot.current_symbol, "sh.600000")
        self.assertEqual(snapshot.live_session, stable_live)
        self.assertIsNone(snapshot.replay_session)
        self.assertIs(snapshot.mode, AppMode.LIVE)
        assert snapshot.live_session is not None
        self.assertEqual(snapshot.live_session.symbol, snapshot.current_symbol)

        self.assertTrue(self.factory.created[1].retired)
        self.assertEqual(self.factory.created[1].retire_count, 1)
        self.assertTrue(self.factory.created[2].retired)
        self.assertEqual(self.factory.created[2].retire_count, 1)
        self.assertFalse(self.factory.created[0].retired)

    def test_factory_failures_preserve_previous_sessions_and_selection(self) -> None:
        initial = self.coordinator.select_symbol("sh.600000")
        old_live = initial.live_session
        assert old_live is not None

        self.factory.fail_next_live = True
        with self.assertRaisesRegex(RuntimeError, "live creation failed"):
            self.coordinator.select_symbol("sz.000001")

        after_switch_failure = self.coordinator.snapshot
        self.assertEqual(after_switch_failure.current_symbol, "sh.600000")
        self.assertEqual(after_switch_failure.live_session, old_live)
        self.assertFalse(self.factory.created[0].retired)
        self.assertEqual(after_switch_failure.session_generation, 2)

        self.factory.fail_next_live = True
        with self.assertRaisesRegex(RuntimeError, "live creation failed"):
            self.coordinator.retry_live()
        self.assertEqual(self.coordinator.snapshot.live_session, old_live)
        self.assertFalse(self.factory.created[0].retired)

        self.coordinator.set_mode("replay")
        replay_before = self.coordinator.begin_replay("2026-07-22")
        old_replay = replay_before.replay_session
        assert old_replay is not None
        self.factory.fail_next_replay = True
        with self.assertRaisesRegex(RuntimeError, "replay creation failed"):
            self.coordinator.begin_replay("2026-07-23")
        self.assertEqual(self.coordinator.snapshot.replay_session, old_replay)
        self.assertFalse(self.factory.created[1].retired)
        self.assertEqual(self.coordinator.snapshot.session_generation, 5)

    def test_activation_failure_on_first_select_symbol_rolls_back_state(self) -> None:
        class _ActivationFailingFactory(_FakeSessionFactory):
            def create_live(self, spec: SessionSpec) -> _FakeSession:
                session = super().create_live(spec)
                session.on_activate = lambda: (_ for _ in ()).throw(
                    RuntimeError("fake activation failed")
                )
                return session

        factory = _ActivationFailingFactory()
        coordinator = AppCoordinator(factory, session_id_factory=_session_id)

        with self.assertRaises(CoordinatorStateError):
            coordinator.select_symbol("sh.600000")

        snapshot = coordinator.snapshot
        self.assertIsNone(snapshot.current_symbol)
        self.assertIsNone(snapshot.live_session)
        self.assertIsNone(snapshot.replay_session)
        self.assertIsNone(snapshot.visible_session)
        self.assertEqual(snapshot.session_generation, 1)
        self.assertEqual(len(factory.created), 1)
        self.assertTrue(factory.created[0].retired)
        self.assertEqual(factory.created[0].retire_count, 1)

    def test_activation_failure_on_retry_live_keeps_previous_live(self) -> None:
        class _ActivationFailingFactory(_FakeSessionFactory):
            def create_live(self, spec: SessionSpec) -> _FakeSession:
                session = super().create_live(spec)
                if spec.generation == 2:
                    session.on_activate = lambda: (_ for _ in ()).throw(
                        RuntimeError("fake activation failed")
                    )
                return session

        factory = _ActivationFailingFactory()
        coordinator = AppCoordinator(factory, session_id_factory=_session_id)

        first = coordinator.select_symbol("sh.600000")
        old_live = first.live_session
        assert old_live is not None
        old_session = factory.created[0]

        with self.assertRaises(CoordinatorStateError):
            coordinator.retry_live()

        snapshot = coordinator.snapshot
        self.assertEqual(snapshot.live_session, old_live)
        self.assertFalse(old_session.retired)
        self.assertEqual(old_session.retire_count, 0)
        self.assertEqual(len(factory.created), 2)
        self.assertTrue(factory.created[1].retired)
        self.assertEqual(factory.created[1].retire_count, 1)

    def test_activation_failure_on_begin_replay_keeps_existing_state(self) -> None:
        class _ActivationFailingFactory(_FakeSessionFactory):
            def create_replay(self, spec: SessionSpec) -> _FakeSession:
                session = super().create_replay(spec)
                session.on_activate = lambda: (_ for _ in ()).throw(
                    RuntimeError("fake activation failed")
                )
                return session

        factory = _ActivationFailingFactory()
        coordinator = AppCoordinator(factory, session_id_factory=_session_id)

        selected = coordinator.select_symbol("sh.600000")
        live = selected.live_session
        assert live is not None
        live_session = factory.created[0]
        coordinator.set_mode("replay")

        with self.assertRaises(CoordinatorStateError):
            coordinator.begin_replay("2026-07-23")

        snapshot = coordinator.snapshot
        self.assertEqual(snapshot.live_session, live)
        self.assertIsNone(snapshot.replay_session)
        self.assertFalse(live_session.retired)
        self.assertTrue(factory.created[1].retired)
        self.assertEqual(factory.created[1].retire_count, 1)

    def test_retirement_runs_outside_state_lock(self) -> None:
        selected = self.coordinator.select_symbol("sh.600000")
        old_live = selected.live_session
        assert old_live is not None
        retirement_started = Event()
        allow_retirement = Event()
        snapshot_read = Event()

        def block_retirement() -> None:
            retirement_started.set()
            allow_retirement.wait(timeout=2)

        self.factory.created[0].on_retire = block_retirement
        retry_thread = Thread(target=self.coordinator.retry_live)
        retry_thread.start()
        self.assertTrue(retirement_started.wait(timeout=1))

        def read_snapshot() -> None:
            self.coordinator.snapshot
            snapshot_read.set()

        reader_thread = Thread(target=read_snapshot)
        reader_thread.start()
        self.assertTrue(snapshot_read.wait(timeout=0.5))
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=old_live.session_id,
                generation=old_live.generation,
            )
        )

        allow_retirement.set()
        retry_thread.join(timeout=1)
        reader_thread.join(timeout=1)
        self.assertFalse(retry_thread.is_alive())
        self.assertFalse(reader_thread.is_alive())

    def test_factory_runs_outside_lock_and_stale_candidate_is_retired(self) -> None:
        selected = self.coordinator.select_symbol("sh.600000")
        old_live = selected.live_session
        assert old_live is not None
        creation_started = Event()
        allow_creation = Event()
        mode_changed = Event()
        retry_errors: list[Exception] = []

        def block_creation(spec: SessionSpec) -> None:
            creation_started.set()
            allow_creation.wait(timeout=2)

        self.factory.on_create_live = block_creation

        def retry_live() -> None:
            try:
                self.coordinator.retry_live()
            except Exception as exc:
                retry_errors.append(exc)

        retry_thread = Thread(target=retry_live)
        retry_thread.start()
        self.assertTrue(creation_started.wait(timeout=1))

        def change_mode() -> None:
            self.coordinator.set_mode("replay")
            mode_changed.set()

        mode_thread = Thread(target=change_mode)
        mode_thread.start()
        lock_was_available = mode_changed.wait(timeout=0.5)
        allow_creation.set()
        retry_thread.join(timeout=1)
        mode_thread.join(timeout=1)

        self.assertTrue(lock_was_available)
        self.assertEqual(len(retry_errors), 1)
        self.assertIsInstance(retry_errors[0], CoordinatorStateError)
        self.assertTrue(self.factory.created[1].retired)
        after = self.coordinator.snapshot
        self.assertIs(after.mode, AppMode.REPLAY)
        self.assertEqual(after.live_session, old_live)

    def test_retirement_failure_is_retained_for_retire_all_retry(self) -> None:
        selected = self.coordinator.select_symbol("sh.600000")
        old_live = selected.live_session
        assert old_live is not None
        self.factory.created[0].retire_failures_remaining = 1

        result = self.coordinator.retry_live()

        replacement = result.live_session
        assert replacement is not None
        self.assertNotEqual(replacement, old_live)
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=old_live.session_id,
                generation=old_live.generation,
            )
        )
        self.assertEqual(self.factory.created[0].retire_count, 1)

        retired = self.coordinator.retire_all()

        self.assertIsNone(retired.live_session)
        self.assertEqual(self.factory.created[0].retire_count, 2)
        self.assertTrue(self.factory.created[0].retired)
        self.assertTrue(self.factory.created[1].retired)

    def test_retire_all_reports_cleanup_failure_without_restoring_sessions(
        self,
    ) -> None:
        selected = self.coordinator.select_symbol("sh.600000")
        live = selected.live_session
        assert live is not None
        self.factory.created[0].retire_failures_remaining = 1

        with self.assertRaises(CoordinatorRetirementError) as raised:
            self.coordinator.retire_all()

        self.assertEqual(len(raised.exception.failures), 1)
        self.assertIsNone(self.coordinator.snapshot.live_session)
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type="live",
                session_id=live.session_id,
                generation=live.generation,
            )
        )

    def test_invalid_inputs_do_not_mutate_state(self) -> None:
        initial = self.coordinator.snapshot
        with self.assertRaises(CoordinatorStateError):
            self.coordinator.begin_replay("2026-07-23")
        with self.assertRaises(CoordinatorValidationError):
            self.coordinator.select_symbol("600000")
        with self.assertRaises(CoordinatorValidationError):
            self.coordinator.set_mode("paper")
        self.assertEqual(self.coordinator.snapshot, initial)

        self.coordinator.set_mode("replay")
        with self.assertRaises(CoordinatorStateError):
            self.coordinator.begin_replay("2026-07-23")
        with self.assertRaises(CoordinatorValidationError):
            self.coordinator.begin_replay("2026-02-30")
        with self.assertRaises(CoordinatorValidationError):
            self.coordinator.begin_replay(datetime(2026, 7, 23, 9, 30))

    def test_retire_all_invalidates_results_without_resetting_app_state(self) -> None:
        self.coordinator.select_symbol("sh.600000")
        self.coordinator.set_mode("replay")
        before = self.coordinator.begin_replay("2026-07-23")
        live = before.live_session
        replay = before.replay_session
        assert live is not None and replay is not None

        after = self.coordinator.retire_all()

        self.assertEqual(after.current_symbol, "sh.600000")
        self.assertIs(after.mode, AppMode.REPLAY)
        self.assertEqual(after.session_generation, 2)
        self.assertIsNone(after.live_session)
        self.assertIsNone(after.replay_session)
        self.assertTrue(all(item.retired for item in self.factory.created))
        for session in (live, replay):
            self.assertFalse(
                self.coordinator.accepts_result(
                    session_type=session.session_type,
                    session_id=session.session_id,
                    generation=session.generation,
                )
            )


class CommitIfAcceptedTests(unittest.TestCase):
    """Regression tests for AppCoordinator.commit_if_accepted.

    These exercises use the real AppCoordinator with its real state lock (not
    a fake), so they cover the atomic acceptance + commit boundary that the
    LiveProjectionStore relies on.
    """

    def setUp(self) -> None:
        self.factory = _FakeSessionFactory()
        self.coordinator = AppCoordinator(
            self.factory,
            session_id_factory=_session_id,
        )
        selected = self.coordinator.select_symbol("sh.600000")
        self.live = selected.live_session
        assert self.live is not None

    def test_wrong_session_id_does_not_run_commit(self) -> None:
        ran: list[bool] = []

        def commit() -> None:
            ran.append(True)

        accepted = self.coordinator.commit_if_accepted(
            session_type="live",
            session_id="live-other",
            generation=self.live.generation,
            commit=commit,
        )
        self.assertFalse(accepted)
        self.assertEqual(ran, [])

    def test_wrong_generation_does_not_run_commit(self) -> None:
        ran: list[bool] = []

        def commit() -> None:
            ran.append(True)

        accepted = self.coordinator.commit_if_accepted(
            session_type="live",
            session_id=self.live.session_id,
            generation=self.live.generation + 1,
            commit=commit,
        )
        self.assertFalse(accepted)
        self.assertEqual(ran, [])

    def test_wrong_session_type_does_not_run_commit(self) -> None:
        ran: list[bool] = []

        def commit() -> None:
            ran.append(True)

        accepted = self.coordinator.commit_if_accepted(
            session_type="replay",
            session_id=self.live.session_id,
            generation=self.live.generation,
            commit=commit,
        )
        self.assertFalse(accepted)
        self.assertEqual(ran, [])

    def test_valid_identity_runs_commit_under_state_lock(self) -> None:
        seen: list[bool] = []

        def commit() -> None:
            # Inside the commit the Session must still be accepted.
            seen.append(
                self.coordinator.accepts_result(
                    session_type="live",
                    session_id=self.live.session_id,
                    generation=self.live.generation,
                )
            )

        accepted = self.coordinator.commit_if_accepted(
            session_type="live",
            session_id=self.live.session_id,
            generation=self.live.generation,
            commit=commit,
        )
        self.assertTrue(accepted)
        self.assertEqual(seen, [True])

    def test_concurrent_session_switch_blocks_until_old_commit_completes(self) -> None:
        # Deterministic interleaving: old Session's commit callback blocks, a
        # concurrent select_symbol(new) must not cross the acceptance boundary
        # until the old commit releases the state lock, and after the switch the
        # old Session's commit must be rejected without running the callback.
        old_live = self.live
        commit_started = Event()
        allow_commit = Event()
        commit_completed: list[bool] = []

        def blocking_commit() -> None:
            commit_started.set()
            allow_commit.wait(timeout=2)
            commit_completed.append(True)

        def old_commit() -> None:
            self.coordinator.commit_if_accepted(
                session_type="live",
                session_id=old_live.session_id,
                generation=old_live.generation,
                commit=blocking_commit,
            )

        commit_thread = Thread(target=old_commit)
        commit_thread.start()
        self.assertTrue(commit_started.wait(timeout=1))

        switch_started = Event()
        switch_done = Event()
        switch_results: list[Any] = []
        switch_errors: list[Exception] = []

        def switch_symbol() -> None:
            switch_started.set()
            try:
                switch_results.append(self.coordinator.select_symbol("sz.000001"))
            except Exception as exc:  # pragma: no cover - surfaced via switch_errors
                switch_errors.append(exc)
            finally:
                switch_done.set()

        switch_thread = Thread(target=switch_symbol)
        switch_thread.start()
        # Wait until the switch thread has actually entered its call, then prove
        # it cannot complete while the old commit still holds the state lock.
        self.assertTrue(switch_started.wait(timeout=1))
        self.assertFalse(switch_done.is_set())
        self.assertTrue(switch_thread.is_alive())

        allow_commit.set()
        commit_thread.join(timeout=2)
        switch_thread.join(timeout=2)
        self.assertFalse(commit_thread.is_alive())
        self.assertFalse(switch_thread.is_alive())
        self.assertTrue(switch_done.is_set())
        self.assertEqual(commit_completed, [True])
        self.assertEqual(switch_errors, [])

        # The new Live Session is now authoritative; the old Session is retired.
        self.assertEqual(len(switch_results), 1)
        new_live = switch_results[0].live_session
        assert new_live is not None
        self.assertNotEqual(new_live.session_id, old_live.session_id)

        ran_after: list[bool] = []

        def commit_after() -> None:
            ran_after.append(True)

        accepted = self.coordinator.commit_if_accepted(
            session_type="live",
            session_id=old_live.session_id,
            generation=old_live.generation,
            commit=commit_after,
        )
        self.assertFalse(accepted)
        self.assertEqual(ran_after, [])

    def test_callback_exception_releases_state_lock(self) -> None:
        ran: list[bool] = []

        def failing_commit() -> None:
            ran.append(True)
            raise RuntimeError("callback failed")

        with self.assertRaises(RuntimeError):
            self.coordinator.commit_if_accepted(
                session_type="live",
                session_id=self.live.session_id,
                generation=self.live.generation,
                commit=failing_commit,
            )
        self.assertEqual(ran, [True])

        # The state lock must have been released: a snapshot read and a fresh
        # commit on the still-current Session must succeed.
        snapshot = self.coordinator.snapshot
        self.assertEqual(snapshot.live_session, self.live)

        ran2: list[bool] = []

        def healthy_commit() -> None:
            ran2.append(True)

        accepted = self.coordinator.commit_if_accepted(
            session_type="live",
            session_id=self.live.session_id,
            generation=self.live.generation,
            commit=healthy_commit,
        )
        self.assertTrue(accepted)
        self.assertEqual(ran2, [True])

    def test_snapshot_is_readable_inside_commit_callback_via_reentrant_lock(self) -> None:
        # commit_if_accepted runs the callback under the state RLock.  snapshot
        # also takes the same RLock, so a read issued from within the callback
        # succeeds by reentrant acquisition (no deadlock) and observes the
        # Session as still accepted.  This is a same-thread reentrancy check,
        # not a cross-thread concurrency assertion.
        snapshot_read: list[Any] = []
        commit_started = Event()
        allow_commit = Event()

        def commit() -> None:
            commit_started.set()
            snapshot_read.append(self.coordinator.snapshot)
            allow_commit.wait(timeout=2)

        commit_thread = Thread(
            target=lambda: self.coordinator.commit_if_accepted(
                session_type="live",
                session_id=self.live.session_id,
                generation=self.live.generation,
                commit=commit,
            )
        )
        commit_thread.start()
        self.assertTrue(commit_started.wait(timeout=1))
        allow_commit.set()
        commit_thread.join(timeout=2)
        self.assertFalse(commit_thread.is_alive())

        self.assertEqual(len(snapshot_read), 1)
        self.assertEqual(snapshot_read[0].live_session, self.live)


if __name__ == "__main__":
    unittest.main()
