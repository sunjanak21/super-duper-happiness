"""Tests for crawler.compute functions."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.compute import (
    compute_concentration_metrics,
    compute_fm_tenure,
    compute_holdings_analysis,
    compute_valuation_ratios,
)
from models.schema import FundManagerInfo, Holding, SectorAllocation


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_holdings(n: int, base_weight: float = 10.0) -> list[Holding]:
    """Create *n* equity holdings with linearly decreasing weights.

    Holding 0: base_weight, Holding 1: base_weight-1, etc.
    """
    return [
        Holding(stock_name=f"Stock {i}", weight_pct=round(base_weight - i, 2))
        for i in range(n)
    ]


# ------------------------------------------------------------------
# compute_concentration_metrics
# ------------------------------------------------------------------

class TestComputeConcentrationMetrics:
    def test_top5_top10_hhi_tail(self):
        # 15 holdings: weights 10, 9, 8, ..., -4 — but negatives don't make
        # financial sense, so use a gentler slope.
        holdings = [
            Holding(stock_name=f"Stock {i}", weight_pct=round(15 - i, 2))
            for i in range(15)
        ]
        # Sorted desc: 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1
        result = compute_concentration_metrics(holdings)

        expected_top5 = 15 + 14 + 13 + 12 + 11  # 65
        expected_top10 = expected_top5 + 10 + 9 + 8 + 7 + 6  # 105
        total = sum(range(1, 16))  # 120
        expected_tail = total - expected_top10  # 15

        assert result["top5_weight_pct"] == expected_top5
        assert result["top10_weight_pct"] == expected_top10
        assert result["tail_weight_pct"] == expected_tail

        # HHI = sum of squared weights
        expected_hhi = sum(w * w for w in range(1, 16))
        assert result["hhi"] == expected_hhi

    def test_fewer_than_10_holdings(self):
        holdings = _make_holdings(3, base_weight=30)
        # Weights: 30, 29, 28
        result = compute_concentration_metrics(holdings)
        assert result["top5_weight_pct"] == 30 + 29 + 28
        assert result["top10_weight_pct"] == 30 + 29 + 28
        assert result["tail_weight_pct"] == 0.0

    def test_empty_holdings(self):
        assert compute_concentration_metrics([]) == {}


# ------------------------------------------------------------------
# compute_holdings_analysis
# ------------------------------------------------------------------

class TestComputeHoldingsAnalysis:
    def test_mixed_instrument_types(self):
        holdings = [
            Holding(stock_name="Equity Co", weight_pct=50.0, instrument_type="equity"),
            Holding(stock_name="Bond Co", weight_pct=30.0, instrument_type="bond"),
            Holding(stock_name="Cash Equiv", weight_pct=10.0, instrument_type="cash"),
            Holding(stock_name="Default Co", weight_pct=10.0),  # no instrument_type -> equity
        ]
        sectors = [
            SectorAllocation(sector_name="Financials", weight_pct=40.0),
            SectorAllocation(sector_name="IT", weight_pct=30.0),
            SectorAllocation(sector_name="Pharma", weight_pct=20.0),
        ]
        result = compute_holdings_analysis(holdings, sectors)

        assert result["all_holdings_count"] == 4
        # equity_count includes explicit equity + unspecified
        assert result["equity_holdings_count"] == 2
        assert result["equity_allocation_pct"] == 60.0
        assert result["debt_allocation_pct"] == 30.0
        assert result["cash_allocation_pct"] == 10.0
        assert result["sector_count"] == 3
        assert result["top3_sector_weight"] == 90.0

    def test_empty(self):
        assert compute_holdings_analysis([], []) == {}

    def test_only_equity(self):
        holdings = [
            Holding(stock_name="A", weight_pct=60.0, instrument_type="equity"),
            Holding(stock_name="B", weight_pct=40.0, instrument_type="equity"),
        ]
        result = compute_holdings_analysis(holdings, [])
        assert result["equity_holdings_count"] == 2
        assert result["debt_allocation_pct"] == 0.0
        assert result["cash_allocation_pct"] == 0.0


# ------------------------------------------------------------------
# compute_valuation_ratios
# ------------------------------------------------------------------

class TestComputeValuationRatios:
    def test_earnings_yield(self):
        result = compute_valuation_ratios(pe=22.5, pb=3.1)
        expected = round(1.0 / 22.5, 6)
        assert result["earnings_yield"] == expected

    def test_pe_none(self):
        result = compute_valuation_ratios(pe=None, pb=3.1)
        assert result["earnings_yield"] is None

    def test_pe_zero(self):
        result = compute_valuation_ratios(pe=0, pb=None)
        assert result["earnings_yield"] is None

    def test_both_none(self):
        result = compute_valuation_ratios(pe=None, pb=None)
        assert result["earnings_yield"] is None


# ------------------------------------------------------------------
# compute_fm_tenure
# ------------------------------------------------------------------

class TestComputeFmTenure:
    def test_two_year_tenure(self):
        managing_since = date(2024, 3, 29)
        factsheet_date = date(2026, 3, 29)
        fm = FundManagerInfo(name="Alice", managing_since=managing_since)
        tenure = compute_fm_tenure([fm], factsheet_date)
        # 2 years = 730 or 731 days depending on leap year; ~2.0
        assert tenure is not None
        assert 1.9 <= tenure <= 2.1

    def test_no_managers(self):
        assert compute_fm_tenure([], date(2025, 3, 31)) is None

    def test_no_managing_since(self):
        fm = FundManagerInfo(name="Bob", managing_since=None)
        assert compute_fm_tenure([fm], date(2025, 3, 31)) is None

    def test_future_managing_since_returns_none(self):
        fm = FundManagerInfo(name="Eve", managing_since=date(2030, 1, 1))
        assert compute_fm_tenure([fm], date(2025, 3, 31)) is None
