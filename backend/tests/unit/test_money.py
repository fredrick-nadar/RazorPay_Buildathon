"""Money primitives: integer paise only; floats and bools are hard-rejected."""

from __future__ import annotations

import pytest

from app.domain.money import (
    MAX_ABS_PAISE,
    MoneyError,
    Paise,
    add_paise,
    format_paise,
    paise_from_decimal_rupees,
    require_paise,
)


class TestRequirePaiseRejectsNonIntegers:
    def test_rejects_fractional_float(self) -> None:
        with pytest.raises(MoneyError, match="float"):
            require_paise(100.5)  # type: ignore[arg-type]

    def test_rejects_integral_float(self) -> None:
        with pytest.raises(MoneyError, match="float"):
            require_paise(100.0)  # type: ignore[arg-type]

    def test_rejects_bool(self) -> None:
        with pytest.raises(MoneyError, match="bool"):
            require_paise(True)  # type: ignore[arg-type]

    def test_rejects_string(self) -> None:
        with pytest.raises(MoneyError, match="str"):
            require_paise("100")  # type: ignore[arg-type]

    def test_rejects_none(self) -> None:
        with pytest.raises(MoneyError, match="NoneType"):
            require_paise(None)  # type: ignore[arg-type]

    def test_rejects_oversized_values(self) -> None:
        with pytest.raises(MoneyError, match="maximum"):
            require_paise(MAX_ABS_PAISE + 1)
        with pytest.raises(MoneyError, match="maximum"):
            require_paise(-(MAX_ABS_PAISE + 1))


class TestRequirePaiseAcceptsSignedIntegers:
    def test_accepts_zero(self) -> None:
        assert require_paise(0) == 0

    def test_accepts_positive_and_negative(self) -> None:
        assert require_paise(7864000) == 7864000
        assert require_paise(-640000) == -640000

    def test_accepts_boundary(self) -> None:
        assert require_paise(MAX_ABS_PAISE) == MAX_ABS_PAISE
        assert require_paise(-MAX_ABS_PAISE) == -MAX_ABS_PAISE


class TestDecimalRupeeParsing:
    @pytest.mark.parametrize(
        ("text", "expected_paise"),
        [
            ("78640.00", 7864000),
            ("78640", 7864000),
            ("0.01", 1),
            ("0.1", 10),
            ("1", 100),
            ("-5.5", -550),
            ("-0.01", -1),
            (" 1234.56 ", 123456),
            ("0", 0),
            ("0.00", 0),
        ],
    )
    def test_exactParsing(self, text: str, expected_paise: int) -> None:
        assert paise_from_decimal_rupees(text) == expected_paise

    @pytest.mark.parametrize(
        "bad",
        [
            "1.234",  # three fractional digits
            "1,234.00",  # grouping separators
            "1e2",  # exponent notation
            "abc",
            "",
            "12 34",
            "  ",
            "--5.00",
            "5.",
            ".5",
            "₹100",
            "100.00 INR",
        ],
    )
    def test_rejects_malformed(self, bad: str) -> None:
        with pytest.raises(MoneyError):
            paise_from_decimal_rupees(bad)

    def test_rejects_non_string_input(self) -> None:
        with pytest.raises(MoneyError, match="str"):
            paise_from_decimal_rupees(1234.56)  # type: ignore[arg-type]


class TestFormattingAndArithmetic:
    def test_format_signed_amounts(self) -> None:
        assert format_paise(Paise(7864000)) == "78640.00 INR"
        assert format_paise(Paise(-1)) == "-0.01 INR"
        assert format_paise(Paise(5)) == "0.05 INR"
        assert format_paise(Paise(-640000), currency="INR") == "-6400.00 INR"

    def test_parse_format_round_trip(self) -> None:
        for text in ["78640.00", "0.01", "-5.50", "0"]:
            parsed = paise_from_decimal_rupees(text)
            normalized = format_paise(parsed).removesuffix(" INR")
            assert paise_from_decimal_rupees(normalized) == parsed

    def test_add_paise_guards_overflow(self) -> None:
        assert add_paise(Paise(100), Paise(-50)) == 50
        with pytest.raises(MoneyError, match="maximum"):
            add_paise(Paise(MAX_ABS_PAISE), Paise(1))

    def test_floats_never_round_trip_through_helpers(self) -> None:
        # Guard the guard: 0.1-style floats must be unusable as money input.
        with pytest.raises(MoneyError):
            require_paise(0.1 * 1000)  # type: ignore[arg-type]
