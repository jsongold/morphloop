"""Unit tests for the generic pydantic-backed validators in ``domains.dns._params``."""

from __future__ import annotations

import pytest

from domains.dns import _params as p
from harness.core.domain_adapter import AdapterParamsError


class TestObj:
    def test_accepts_mapping(self) -> None:
        assert p.obj({"a": 1}, "where") == {"a": 1}

    @pytest.mark.parametrize("value", [[1, 2], "x", 1, None])
    def test_rejects_non_mapping(self, value: object) -> None:
        with pytest.raises(AdapterParamsError, match="where must be an object"):
            p.obj(value, "where")  # type: ignore[arg-type]


class TestOnlyKeys:
    def test_accepts_required_and_optional(self) -> None:
        p.only_keys({"a": 1, "b": 2}, "where", required={"a"}, optional={"b"})

    def test_accepts_missing_optional(self) -> None:
        p.only_keys({"a": 1}, "where", required={"a"}, optional={"b"})

    def test_rejects_missing_required(self) -> None:
        with pytest.raises(AdapterParamsError, match="where is missing a"):
            p.only_keys({"b": 2}, "where", required={"a"}, optional={"b"})

    def test_rejects_unknown_keys(self) -> None:
        with pytest.raises(AdapterParamsError, match="where has unknown keys: c"):
            p.only_keys({"a": 1, "c": 2}, "where", required={"a"}, optional={"b"})

    def test_missing_wins_over_unknown(self) -> None:
        with pytest.raises(AdapterParamsError, match="is missing a"):
            p.only_keys({"c": 2}, "where", required={"a"}, optional=set())

    def test_no_keys_required_or_optional(self) -> None:
        p.only_keys({}, "where", required=set(), optional=set())
        with pytest.raises(AdapterParamsError, match="has unknown keys: x"):
            p.only_keys({"x": 1}, "where", required=set(), optional=set())


class TestArray:
    def test_accepts_list_meeting_min_items(self) -> None:
        assert p.array([1, 2], "where", min_items=1) == [1, 2]

    def test_accepts_empty_list_with_min_items_zero(self) -> None:
        assert p.array([], "where", min_items=0) == []

    @pytest.mark.parametrize("value", ["abc", b"abc", 1, {"a": 1}, None])
    def test_rejects_non_array(self, value: object) -> None:
        with pytest.raises(AdapterParamsError, match="where must be an array"):
            p.array(value, "where", min_items=0)  # type: ignore[arg-type]

    def test_rejects_below_min_items(self) -> None:
        with pytest.raises(AdapterParamsError, match=r"where must have at least 2 item\(s\)"):
            p.array([1], "where", min_items=2)


class TestString:
    def test_accepts_non_empty_string(self) -> None:
        assert p.string("abc", "where") == "abc"

    @pytest.mark.parametrize("value", ["", 1, 1.5, True, b"abc", None])
    def test_rejects_empty_or_non_string(self, value: object) -> None:
        with pytest.raises(AdapterParamsError, match="where must be a non-empty string"):
            p.string(value, "where")  # type: ignore[arg-type]


class TestInteger:
    def test_accepts_value_in_range(self) -> None:
        assert p.integer(5, "where", minimum=1, maximum=10) == 5

    @pytest.mark.parametrize("value", [True, False, 5.0, "5", None])
    def test_rejects_non_integer(self, value: object) -> None:
        with pytest.raises(AdapterParamsError, match="where must be an integer"):
            p.integer(value, "where", minimum=1, maximum=10)  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", [0, 11])
    def test_rejects_out_of_range(self, value: int) -> None:
        with pytest.raises(AdapterParamsError, match="where must be between 1 and 10"):
            p.integer(value, "where", minimum=1, maximum=10)


class TestEnum:
    def test_accepts_a_choice(self) -> None:
        assert p.enum("A", "where", ("A", "B")) == "A"

    def test_rejects_a_non_choice(self) -> None:
        with pytest.raises(AdapterParamsError, match="where must be one of A, B"):
            p.enum("C", "where", ("A", "B"))

    def test_rejects_non_string(self) -> None:
        with pytest.raises(AdapterParamsError, match="where must be one of A, B"):
            p.enum(1, "where", ("A", "B"))  # type: ignore[arg-type]

    def test_single_choice(self) -> None:
        assert p.enum("A", "where", ("A",)) == "A"
