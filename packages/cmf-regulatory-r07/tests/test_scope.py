"""Pruebas del alcance de libros (BOOK) por seccion del R07."""

from __future__ import annotations

import pytest
from cmf_regulatory_r07.config.scope import (
    BOOK_SCOPE,
    BOOKS_TASAS,
    SECTION_TASAS,
    books_for_section,
)


def test_tasas_section_is_trading_only() -> None:
    assert SECTION_TASAS == "s02"
    assert BOOKS_TASAS == ("TRADING",)
    assert BOOK_SCOPE["s02"] == ("TRADING",)


def test_book_scope_defined_per_section() -> None:
    assert set(BOOK_SCOPE) == {"s02", "s03", "s04", "s05"}
    assert books_for_section("s03") == ("TRADING",)
    assert books_for_section("s04") == ("TRADING", "BANKING")
    assert books_for_section("s05") == ("TRADING", "BANKING")


def test_unknown_section_raises() -> None:
    with pytest.raises(ValueError, match="unknown R07 section"):
        books_for_section("s99")
