"""Tests for crawler.normalizer functions."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.normalizer import (
    deduplicate,
    normalize_aum,
    normalize_date,
    normalize_fund_manager_name,
    normalize_percentage,
    normalize_scheme_name,
    normalize_sector_name,
)
from models.schema import SchemeFactsheet


# ------------------------------------------------------------------
# normalize_scheme_name
# ------------------------------------------------------------------

class TestNormalizeSchemeNameDirect:
    def test_direct_plan_growth_option(self):
        result = normalize_scheme_name(
            "HDFC Flexi Cap Fund - Direct Plan - Growth Option"
        )
        assert "Direct" in result
        assert "Growth" in result
        # Should NOT contain "Plan" or "Option" as standalone artefacts
        assert "Plan" not in result
        assert "Option" not in result

    def test_abbreviations_expanded(self):
        result = normalize_scheme_name("SBI Large Cap Fund-Dir-Gr")
        assert "Direct" in result
        assert "Growth" in result


class TestNormalizeSchemeNameEdge:
    def test_empty_string(self):
        assert normalize_scheme_name("") == ""

    def test_regular_plan(self):
        result = normalize_scheme_name("ICICI Pru Value Fund - Regular Plan - Growth")
        assert "Regular" in result
        assert "Growth" in result


# ------------------------------------------------------------------
# normalize_percentage
# ------------------------------------------------------------------

class TestNormalizePercentage:
    def test_string_with_percent(self):
        assert normalize_percentage("45.2%") == 45.2

    def test_decimal_fraction(self):
        assert normalize_percentage(0.452) == 45.2

    def test_already_percentage(self):
        assert normalize_percentage(45.2) == 45.2

    def test_none_input(self):
        assert normalize_percentage(None) is None

    def test_empty_string(self):
        assert normalize_percentage("") is None

    def test_zero(self):
        assert normalize_percentage(0) == 0


# ------------------------------------------------------------------
# normalize_aum
# ------------------------------------------------------------------

class TestNormalizeAum:
    def test_lakhs_hint(self):
        result = normalize_aum(5000, unit_hint="lakhs")
        assert result == 50.0  # 5000 lakhs = 50 crores

    def test_crores_hint(self):
        result = normalize_aum(250.5, unit_hint="crores")
        assert result == 250.5

    def test_absolute_hint(self):
        result = normalize_aum(2_500_000_000, unit_hint="absolute")
        assert result == 250.0  # 2.5 billion / 1e7 = 250 crores

    def test_auto_detect_large_value(self):
        # > 1e6 treated as absolute rupees
        result = normalize_aum(25_000_000_000)
        assert result == 2500.0

    def test_auto_detect_medium_value(self):
        # > 1e4 treated as lakhs
        result = normalize_aum(50000)
        assert result == 500.0

    def test_auto_detect_crores(self):
        # <= 1e4 treated as crores
        result = normalize_aum(250.5)
        assert result == 250.5

    def test_none(self):
        assert normalize_aum(None) is None

    def test_string_with_comma(self):
        result = normalize_aum("25,432.50", unit_hint="cr")
        assert result == 25432.5


# ------------------------------------------------------------------
# normalize_date
# ------------------------------------------------------------------

class TestNormalizeDate:
    def test_dd_mm_yyyy_dash(self):
        assert normalize_date("31-03-2025") == date(2025, 3, 31)

    def test_month_name_long(self):
        assert normalize_date("March 31, 2025") == date(2025, 3, 31)

    def test_dd_mm_yyyy_slash(self):
        assert normalize_date("31/03/2025") == date(2025, 3, 31)

    def test_iso(self):
        assert normalize_date("2025-03-31") == date(2025, 3, 31)

    def test_dd_mon_yyyy(self):
        assert normalize_date("31-Mar-2025") == date(2025, 3, 31)

    def test_already_date(self):
        d = date(2025, 3, 31)
        assert normalize_date(d) == d

    def test_none(self):
        assert normalize_date(None) is None

    def test_unparseable(self):
        assert normalize_date("not-a-date") is None


# ------------------------------------------------------------------
# normalize_fund_manager_name
# ------------------------------------------------------------------

class TestNormalizeFundManagerName:
    def test_strip_cfa(self):
        result = normalize_fund_manager_name("JOHN DOE, CFA")
        assert result == "John Doe"

    def test_strip_parenthesized_mba(self):
        result = normalize_fund_manager_name("Jane Smith (MBA)")
        assert result == "Jane Smith"

    def test_multiple_designations(self):
        result = normalize_fund_manager_name("Robert Brown, CFA, MBA")
        assert result == "Robert Brown"

    def test_empty_string(self):
        assert normalize_fund_manager_name("") == ""

    def test_no_designation(self):
        result = normalize_fund_manager_name("Alice Cooper")
        assert result == "Alice Cooper"


# ------------------------------------------------------------------
# normalize_sector_name
# ------------------------------------------------------------------

class TestNormalizeSectorName:
    def test_mapped_variant(self):
        mapping = {"financials": "Financial Services", "bfsi": "Financial Services"}
        assert normalize_sector_name("Financials", mapping) == "Financial Services"

    def test_mapped_variant_bfsi(self):
        mapping = {"financials": "Financial Services", "bfsi": "Financial Services"}
        assert normalize_sector_name("BFSI", mapping) == "Financial Services"

    def test_unmapped_returns_title(self):
        assert normalize_sector_name("consumer goods", {}) == "Consumer Goods"

    def test_empty_string(self):
        assert normalize_sector_name("", {}) == ""


# ------------------------------------------------------------------
# deduplicate
# ------------------------------------------------------------------

class TestDeduplicate:
    def _make_scheme(self, name, confidence):
        return SchemeFactsheet(
            amc_name="TestAMC",
            scheme_name=name,
            factsheet_date=date(2025, 3, 31),
            parse_confidence=confidence,
        )

    def test_no_duplicates(self):
        schemes = [
            self._make_scheme("Fund A", 0.8),
            self._make_scheme("Fund B", 0.7),
        ]
        result = deduplicate(schemes)
        assert len(result) == 2

    def test_higher_confidence_wins(self):
        low = self._make_scheme("Fund A", 0.5)
        high = self._make_scheme("Fund A", 0.9)
        result = deduplicate([low, high])
        assert len(result) == 1
        assert result[0].parse_confidence == 0.9

    def test_higher_confidence_wins_reversed_order(self):
        high = self._make_scheme("Fund A", 0.9)
        low = self._make_scheme("Fund A", 0.5)
        result = deduplicate([high, low])
        assert len(result) == 1
        assert result[0].parse_confidence == 0.9

    def test_empty_list(self):
        assert deduplicate([]) == []
