"""Computation layer for derived metrics on scheme factsheets."""

import logging
from datetime import date
from typing import Optional

from models.schema import (
    EnrichedSchemeFactsheet,
    FundManagerInfo,
    Holding,
    SchemeFactsheet,
    SectorAllocation,
)

logger = logging.getLogger(__name__)


def compute_derived_metrics(
    schemes: list[SchemeFactsheet], config: dict
) -> list[EnrichedSchemeFactsheet]:
    """Compute all derived metrics for a list of scheme factsheets.

    Produces EnrichedSchemeFactsheet objects with concentration metrics,
    holdings analysis, valuation ratios, and fund manager tenure.

    Args:
        schemes: Normalized SchemeFactsheet objects.
        config: Pipeline configuration dict.

    Returns:
        List of EnrichedSchemeFactsheet with computed fields populated.
    """
    enriched: list[EnrichedSchemeFactsheet] = []

    for scheme in schemes:
        try:
            data = scheme.model_dump()
            enriched_scheme = EnrichedSchemeFactsheet(**data)

            # Concentration metrics
            concentration = compute_concentration_metrics(
                scheme.top_holdings
            )
            enriched_scheme.top5_weight_pct = concentration.get(
                "top5_weight_pct"
            )
            enriched_scheme.top10_weight_pct = concentration.get(
                "top10_weight_pct"
            )
            enriched_scheme.hhi = concentration.get("hhi")
            enriched_scheme.tail_weight_pct = concentration.get(
                "tail_weight_pct"
            )

            # Holdings analysis
            analysis = compute_holdings_analysis(
                scheme.top_holdings, scheme.sector_allocation
            )
            enriched_scheme.equity_holdings_count = analysis.get(
                "equity_holdings_count"
            )
            enriched_scheme.all_holdings_count = analysis.get(
                "all_holdings_count"
            )
            enriched_scheme.equity_allocation_pct = analysis.get(
                "equity_allocation_pct"
            )
            enriched_scheme.debt_allocation_pct = analysis.get(
                "debt_allocation_pct"
            )
            enriched_scheme.cash_allocation_pct = analysis.get(
                "cash_allocation_pct"
            )
            enriched_scheme.sector_count = analysis.get("sector_count")
            enriched_scheme.top3_sector_weight_pct = analysis.get(
                "top3_sector_weight"
            )

            # Valuation ratios
            valuation = compute_valuation_ratios(
                scheme.portfolio_pe, scheme.portfolio_pb
            )
            enriched_scheme.earnings_yield = valuation.get("earnings_yield")

            # Fund manager tenure
            enriched_scheme.fm_tenure_years = compute_fm_tenure(
                scheme.fund_managers, scheme.factsheet_date
            )

            enriched.append(enriched_scheme)
        except Exception:
            logger.exception(
                "Failed to compute derived metrics for: %s",
                scheme.scheme_name,
            )

    logger.info(
        "Computed derived metrics for %d / %d schemes",
        len(enriched),
        len(schemes),
    )
    return enriched


def compute_concentration_metrics(holdings: list[Holding]) -> dict:
    """Compute portfolio concentration metrics from holdings.

    Args:
        holdings: List of Holding objects, assumed sorted by weight descending
                  or will be sorted internally.

    Returns:
        Dict with keys:
            - top5_weight_pct: Sum of top 5 holding weights.
            - top10_weight_pct: Sum of top 10 holding weights.
            - hhi: Herfindahl-Hirschman Index (sum of squared weights).
            - tail_weight_pct: Sum of weights beyond top 10.
    """
    if not holdings:
        return {}

    sorted_holdings = sorted(
        holdings, key=lambda h: h.weight_pct, reverse=True
    )
    weights = [h.weight_pct for h in sorted_holdings]

    top5_weight = sum(weights[:5])
    top10_weight = sum(weights[:10])
    total_weight = sum(weights)
    tail_weight = total_weight - top10_weight if len(weights) > 10 else 0.0

    # HHI: sum of squared weights (using percentage points)
    hhi = sum(w * w for w in weights)

    return {
        "top5_weight_pct": round(top5_weight, 4),
        "top10_weight_pct": round(top10_weight, 4),
        "hhi": round(hhi, 4),
        "tail_weight_pct": round(tail_weight, 4),
    }


def compute_holdings_analysis(
    holdings: list[Holding],
    sector_allocation: list[SectorAllocation],
) -> dict:
    """Analyse holdings composition and sector breakdown.

    Args:
        holdings: List of Holding objects.
        sector_allocation: List of SectorAllocation objects.

    Returns:
        Dict with keys:
            - equity_holdings_count: Number of equity holdings.
            - all_holdings_count: Total number of holdings.
            - equity_allocation_pct: Total equity weight %.
            - debt_allocation_pct: Total debt weight %.
            - cash_allocation_pct: Total cash weight %.
            - sector_count: Number of distinct sectors.
            - top3_sector_weight: Sum of top 3 sector weights.
    """
    if not holdings and not sector_allocation:
        return {}

    all_count = len(holdings)

    equity_keywords = {"equity", "stock", "share"}
    debt_keywords = {"debt", "bond", "debenture", "ncd", "fixed income", "government security", "g-sec"}
    cash_keywords = {"cash", "cash equivalent", "cash & cash equivalent", "money market", "treps", "cblo", "repo"}

    equity_count = 0
    equity_weight = 0.0
    debt_weight = 0.0
    cash_weight = 0.0

    for h in holdings:
        itype = (h.instrument_type or "").strip().lower()
        if itype in equity_keywords or itype == "":
            # Default to equity when instrument_type is unspecified
            equity_count += 1
            equity_weight += h.weight_pct
        elif itype in debt_keywords:
            debt_weight += h.weight_pct
        elif itype in cash_keywords:
            cash_weight += h.weight_pct
        else:
            # Unknown type -- still count towards total
            equity_count += 1
            equity_weight += h.weight_pct

    # Sector analysis
    sector_count = len(sector_allocation)
    sorted_sectors = sorted(
        sector_allocation, key=lambda s: s.weight_pct, reverse=True
    )
    top3_sector_weight = sum(s.weight_pct for s in sorted_sectors[:3])

    return {
        "equity_holdings_count": equity_count,
        "all_holdings_count": all_count,
        "equity_allocation_pct": round(equity_weight, 4),
        "debt_allocation_pct": round(debt_weight, 4),
        "cash_allocation_pct": round(cash_weight, 4),
        "sector_count": sector_count,
        "top3_sector_weight": round(top3_sector_weight, 4),
    }


def compute_valuation_ratios(
    pe: Optional[float], pb: Optional[float]
) -> dict:
    """Compute derived valuation ratios.

    Args:
        pe: Portfolio P/E ratio (price-to-earnings).
        pb: Portfolio P/B ratio (price-to-book).

    Returns:
        Dict with keys:
            - earnings_yield: 1/PE if PE is available and positive, else None.
    """
    result: dict = {}

    if pe is not None and pe > 0:
        result["earnings_yield"] = round(1.0 / pe, 6)
    else:
        result["earnings_yield"] = None

    return result


def compute_fm_tenure(
    fund_managers: list[FundManagerInfo], factsheet_date: date
) -> Optional[float]:
    """Compute tenure of the primary fund manager in years.

    The primary fund manager is assumed to be the first in the list.
    Tenure is calculated from managing_since to factsheet_date.

    Args:
        fund_managers: List of FundManagerInfo; first entry is the primary FM.
        factsheet_date: Date of the factsheet for relative calculation.

    Returns:
        Tenure in years (float), or None if unavailable.
    """
    if not fund_managers:
        return None

    primary = fund_managers[0]
    if primary.managing_since is None:
        return None

    delta = factsheet_date - primary.managing_since
    if delta.days < 0:
        logger.warning(
            "Fund manager %s has managing_since (%s) after factsheet_date (%s)",
            primary.name,
            primary.managing_since,
            factsheet_date,
        )
        return None

    tenure_years = round(delta.days / 365.25, 2)
    return tenure_years
