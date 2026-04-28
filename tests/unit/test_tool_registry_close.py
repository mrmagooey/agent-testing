"""Unit tests for ToolRegistry.close() — resource cleanup contract."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sec_review_framework.tools.registry import ToolRegistry


class TestCloseInvokesAllClosers:
    def test_single_closer_called_once(self) -> None:
        registry = ToolRegistry()
        fn = MagicMock()
        registry.add_closer(fn)
        registry.close()
        fn.assert_called_once_with()

    def test_multiple_closers_all_called(self) -> None:
        registry = ToolRegistry()
        fns = [MagicMock() for _ in range(3)]
        for fn in fns:
            registry.add_closer(fn)
        registry.close()
        for fn in fns:
            fn.assert_called_once_with()

    def test_closers_called_in_lifo_order(self) -> None:
        registry = ToolRegistry()
        order: list[int] = []
        registry.add_closer(lambda: order.append(1))
        registry.add_closer(lambda: order.append(2))
        registry.add_closer(lambda: order.append(3))
        registry.close()
        assert order == [3, 2, 1]

    def test_empty_closers_does_not_raise(self) -> None:
        registry = ToolRegistry()
        registry.close()


class TestCloseIdempotency:
    def test_second_close_is_noop(self) -> None:
        registry = ToolRegistry()
        fn = MagicMock()
        registry.add_closer(fn)
        registry.close()
        registry.close()
        fn.assert_called_once_with()

    def test_many_closes_still_call_once(self) -> None:
        registry = ToolRegistry()
        fn = MagicMock()
        registry.add_closer(fn)
        for _ in range(10):
            registry.close()
        fn.assert_called_once_with()

    def test_second_close_does_not_raise(self) -> None:
        registry = ToolRegistry()
        registry.close()
        registry.close()


class TestClosePartialFailureIsolation:
    def test_exception_in_one_closer_does_not_prevent_others(self) -> None:
        registry = ToolRegistry()
        after = MagicMock()
        registry.add_closer(after)
        registry.add_closer(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        registry.close()
        after.assert_called_once_with()

    def test_all_closers_run_even_if_all_raise(self) -> None:
        registry = ToolRegistry()
        calls: list[int] = []

        def raiser(n: int):
            def _fn():
                calls.append(n)
                raise ValueError(f"error {n}")
            return _fn

        for i in range(3):
            registry.add_closer(raiser(i))
        registry.close()
        assert sorted(calls) == [0, 1, 2]

    def test_exception_in_closer_does_not_propagate(self) -> None:
        registry = ToolRegistry()
        registry.add_closer(lambda: (_ for _ in ()).throw(RuntimeError("oops")))
        registry.close()

    def test_first_closer_exception_remaining_closers_still_run_lifo(self) -> None:
        registry = ToolRegistry()
        order: list[int] = []

        def good(n: int):
            def _fn():
                order.append(n)
            return _fn

        def bad():
            raise RuntimeError("bad closer")

        registry.add_closer(good(1))
        registry.add_closer(bad)
        registry.add_closer(good(3))
        registry.close()
        assert order == [3, 1]

    def test_exception_logged_not_silently_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        registry = ToolRegistry()
        registry.add_closer(lambda: (_ for _ in ()).throw(RuntimeError("logged")))
        with caplog.at_level(logging.ERROR, logger="sec_review_framework.tools.registry"):
            registry.close()
        assert any("logged" in r.message or "logged" in str(r.exc_info) for r in caplog.records)


class TestCloseWithNoRegisteredTools:
    def test_close_on_fresh_registry_is_harmless(self) -> None:
        registry = ToolRegistry()
        registry.close()
        assert registry._closed is True

    def test_closed_flag_set_after_close(self) -> None:
        registry = ToolRegistry()
        assert registry._closed is False
        registry.close()
        assert registry._closed is True

    def test_closed_flag_not_reset_on_second_call(self) -> None:
        registry = ToolRegistry()
        registry.close()
        registry.close()
        assert registry._closed is True
