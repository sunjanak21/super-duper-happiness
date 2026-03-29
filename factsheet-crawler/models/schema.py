"""Pydantic models for mutual fund factsheet data."""

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DownloadStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DownloadResult:
    """Result of a single PDF download attempt."""

    path: Optional[str]
    amc_slug: str
    amc_name: str
    date_str: str
    status: DownloadStatus
    error: Optional[str] = None


class Holding(BaseModel):
    """A single portfolio holding."""

    stock_name: str
    sector: Optional[str] = None
    weight_pct: float  # percentage of portfolio, e.g. 8.5
    instrument_type: Optional[str] = None  # equity, debt, cash, etc.


class SectorAllocation(BaseModel):
    """Sector weight in portfolio."""

    sector_name: str
    weight_pct: float


class FundManagerInfo(BaseModel):
    """Fund manager details."""

    name: str
    managing_since: Optional[date] = None
    experience_years: Optional[float] = None


class SchemeFactsheet(BaseModel):
    """One record per scheme per factsheet date."""

    amc_name: str
    scheme_name: str
    scheme_category: Optional[str] = None
    amfi_code: Optional[str] = None
    factsheet_date: date

    # Fund manager
    fund_managers: list[FundManagerInfo] = Field(default_factory=list)

    # Portfolio metrics
    portfolio_turnover_pct: Optional[float] = None
    aum_cr: Optional[float] = None  # AUM in crores
    expense_ratio_pct: Optional[float] = None

    # Holdings
    top_holdings: list[Holding] = Field(default_factory=list)
    total_holdings_count: Optional[int] = None

    # Sector allocation
    sector_allocation: list[SectorAllocation] = Field(default_factory=list)

    # Valuation (equity schemes only)
    portfolio_pe: Optional[float] = None
    portfolio_pb: Optional[float] = None

    # NAV
    nav: Optional[float] = None
    nav_date: Optional[date] = None

    # Benchmark
    benchmark: Optional[str] = None
    inception_date: Optional[date] = None

    # Metadata
    source_pdf_path: Optional[str] = None
    parse_confidence: Optional[float] = None
    parse_warnings: list[str] = Field(default_factory=list)


class EnrichedSchemeFactsheet(SchemeFactsheet):
    """SchemeFactsheet with derived/computed metrics."""

    # Concentration metrics
    top5_weight_pct: Optional[float] = None
    top10_weight_pct: Optional[float] = None
    hhi: Optional[float] = None
    tail_weight_pct: Optional[float] = None

    # Holdings analysis
    equity_holdings_count: Optional[int] = None
    all_holdings_count: Optional[int] = None
    equity_allocation_pct: Optional[float] = None
    debt_allocation_pct: Optional[float] = None
    cash_allocation_pct: Optional[float] = None
    sector_count: Optional[int] = None
    top3_sector_weight_pct: Optional[float] = None

    # Derived ratios
    earnings_yield: Optional[float] = None

    # Fund manager metrics
    fm_tenure_years: Optional[float] = None
