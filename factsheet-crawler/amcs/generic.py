"""Generic fallback factsheet parser.

Uses broad, flexible regex patterns that aim to work reasonably well
for any AMC's factsheet PDF.  This parser is used when no AMC-specific
subclass is available.
"""

import logging
import re
from datetime import date
from typing import Optional

from models.schema import (
    FundManagerInfo,
    Holding,
    SectorAllocation,
)

from .base import BaseAMCParser

logger = logging.getLogger(__name__)


class GenericAMCParser(BaseAMCParser):
    """Broad-heuristic parser that handles most AMC factsheet formats.

    Every ``extract_*`` method widens the regex net compared to the base
    class, accepting more varied layouts at the cost of slightly lower
    precision.  No method is left abstract -- this class is always usable
    as a concrete fallback.
    """

    # ------------------------------------------------------------------
    # Broader regex patterns
    # ------------------------------------------------------------------

    # Scheme heading: accept any line that looks like a fund name
    RE_SCHEME_HEADING = re.compile(
        r"^(?P<name>"
        r"[A-Z][A-Za-z ]{3,}"                         # starts with capital
        r"(?:Fund|Scheme|Plan|Portfolio|Growth|Dividend|IDCW|Direct|Regular"
        r"|Advantage|Opportunities|Savings|Income|Balanced|Flexi|Multi)"
        r"[A-Za-z \-/()]*"
        r")\s*$",
        re.MULTILINE,
    )

    # Fund manager: very flexible, accept various label forms
    RE_FUND_MANAGER_GENERIC = re.compile(
        r"(?:Fund\s*Manager\(?s?\)?|Managed\s*[Bb]y|Portfolio\s*Manager)"
        r"\s*[:\-]?\s*"
        r"(?:Mr\.?\s*|Ms\.?\s*|Shri\.?\s*|Dr\.?\s*)?"
        r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
        re.IGNORECASE,
    )

    # AUM: accept many label variations
    RE_AUM_GENERIC = re.compile(
        r"(?:AUM|Assets?\s*Under\s*Management|Net\s*Assets|Fund\s*Size|Corpus)"
        r"\s*(?:\(.*?\))?\s*[:\-]?\s*"
        r"(?:Rs\.?\s*|INR\s*|`\s*)?"
        r"(?P<value>[\d,]+(?:\.\d+)?)\s*"
        r"(?:Cr(?:ores?)?|Crore|Lakh\s*Crore)",
        re.IGNORECASE,
    )

    # NAV: more permissive
    RE_NAV_GENERIC = re.compile(
        r"(?:NAV|Net\s*Asset\s*Value)\s*(?:\([^)]*\))?\s*[:\-]?\s*"
        r"(?:Rs\.?\s*|INR\s*|`\s*)?"
        r"(?P<value>[\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    # Expense ratio: handle "TER" abbreviation as well
    RE_EXPENSE_GENERIC = re.compile(
        r"(?:Total\s*Expense\s*Ratio|TER|Expense\s*Ratio)"
        r"\s*(?:\([^)]*\))?\s*[:\-]?\s*"
        r"(?P<value>[\d.]+)\s*%",
        re.IGNORECASE,
    )

    # Benchmark: handle "Benchmark Index" label
    RE_BENCHMARK_GENERIC = re.compile(
        r"(?:Benchmark(?:\s*Index)?)\s*[:\-]?\s*"
        r"(?P<name>[A-Za-z0-9 &\-/().,]+?)(?:\n|$)",
        re.IGNORECASE,
    )

    # Holdings row: minimal -- just name followed by a number
    RE_HOLDING_ROW_GENERIC = re.compile(
        r"(?P<name>[A-Za-z .'&()]{4,50}?)\s{2,}"
        r"(?:(?P<sector>[A-Za-z &/\-]{3,30}?)\s{2,})?"
        r"(?P<pct>[\d]+\.[\d]{1,2})\s*%?",
    )

    # Sector line: very flexible
    RE_SECTOR_LINE_GENERIC = re.compile(
        r"(?P<sector>[A-Za-z &/\-]{3,40}?)\s+"
        r"(?P<pct>[\d]+\.[\d]{1,2})\s*%",
    )

    # Turnover: also accept "times" suffix
    RE_TURNOVER_GENERIC = re.compile(
        r"(?:Portfolio\s*Turnover|Turnover\s*Ratio)"
        r"\s*(?:Ratio)?\s*[:\-]?\s*"
        r"(?P<value>[\d.]+)\s*(?:%|times?)?",
        re.IGNORECASE,
    )

    # Category: widened
    RE_CATEGORY_GENERIC = re.compile(
        r"(?:Category|Scheme\s*Type|Fund\s*Type|Scheme\s*Category|Type\s*of\s*Scheme)"
        r"\s*[:\-]?\s*(?P<cat>[A-Za-z ()\-–/&]+?)(?:\n|$)",
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Overridden methods
    # ------------------------------------------------------------------

    def extract_fund_managers(self, text: str) -> list[FundManagerInfo]:
        """Extract fund managers using broad heuristics."""
        logger.debug("Generic: extracting fund managers")
        managers: list[FundManagerInfo] = []
        seen: set[str] = set()
        try:
            for m in self.RE_FUND_MANAGER_GENERIC.finditer(text):
                name = m.group("name").strip()
                if not name or len(name) < 3 or name in seen:
                    continue
                seen.add(name)
                info = FundManagerInfo(name=name)

                region_start = text.find(name)
                region = text[region_start: region_start + 400] if region_start >= 0 else ""

                since_m = self.RE_MANAGING_SINCE.search(region)
                if since_m:
                    info.managing_since = self._parse_date(since_m.group("date"))

                exp_m = self.RE_EXPERIENCE.search(region)
                if exp_m:
                    info.experience_years = self._safe_float(exp_m.group("years"))

                managers.append(info)
                logger.debug("Generic: found fund manager %s", info.name)
        except Exception:
            logger.exception("Generic: error extracting fund managers")

        if not managers:
            return super().extract_fund_managers(text)
        return managers

    def extract_portfolio_turnover(self, text: str) -> Optional[float]:
        """Extract portfolio turnover with flexible matching."""
        logger.debug("Generic: extracting portfolio turnover")
        try:
            m = self.RE_TURNOVER_GENERIC.search(text)
            if m:
                val = self._safe_float(m.group("value"))
                if val is None:
                    return None
                # Convert ratio to percentage if it looks like a ratio
                full_match = m.group(0).lower()
                if val <= 5.0 and "times" in full_match:
                    val = round(val * 100, 2)
                logger.debug("Generic: portfolio turnover: %s%%", val)
                return val
        except Exception:
            logger.exception("Generic: error extracting portfolio turnover")
        return super().extract_portfolio_turnover(text)

    def extract_holdings(
        self,
        text: str,
        pages: dict[int, str],
        pdf_path: str | None = None,
    ) -> list[Holding]:
        """Extract holdings using broad heuristics.

        Attempts pdfplumber, then the generic regex, then the base.
        """
        logger.debug("Generic: extracting holdings")
        holdings: list[Holding] = []

        # pdfplumber tables
        if pdf_path:
            try:
                tables_by_page = self._extract_tables_pdfplumber(pdf_path)
                for _pn, tables in tables_by_page.items():
                    for table in tables:
                        holdings.extend(self._parse_holdings_table(table))
                if holdings:
                    logger.debug(
                        "Generic: extracted %d holdings via pdfplumber",
                        len(holdings),
                    )
                    return holdings
            except Exception:
                logger.exception("Generic: pdfplumber extraction failed")

        # Generic regex
        try:
            full = text if isinstance(text, str) else self._full_text(pages)
            for m in self.RE_HOLDING_ROW_GENERIC.finditer(full):
                name = m.group("name").strip()
                # Skip obvious non-holding lines
                if any(kw in name.lower() for kw in (
                    "total", "net asset", "grand total", "sub total",
                    "cash", "net receivable",
                )):
                    continue
                pct = self._safe_float(m.group("pct"))
                if pct is None or pct <= 0 or pct > 100:
                    continue
                sector = None
                if m.group("sector"):
                    sector = m.group("sector").strip()
                holdings.append(
                    Holding(stock_name=name, sector=sector, weight_pct=pct),
                )
            if holdings:
                logger.debug(
                    "Generic: extracted %d holdings via regex", len(holdings),
                )
                return holdings
        except Exception:
            logger.exception("Generic: regex extraction failed")

        return super().extract_holdings(text, pages, pdf_path=pdf_path)

    def extract_sector_allocation(self, text: str) -> list[SectorAllocation]:
        """Extract sector allocation with broader section detection."""
        logger.debug("Generic: extracting sector allocation")
        allocations: list[SectorAllocation] = []
        try:
            # Try to find the sector allocation section
            section_pattern = re.compile(
                r"(?:Sector(?:al)?\s*(?:Allocation|Wise|Break|Composition)"
                r"|Industry\s*(?:Allocation|Wise|Break|Composition))"
                r".*?(?=\n\s*\n(?:[A-Z]|\Z))",
                re.IGNORECASE | re.DOTALL,
            )
            section_m = section_pattern.search(text)
            search_text = section_m.group(0) if section_m else text

            noise_words = {
                "sector", "total", "allocation", "industry", "composition",
                "wise", "break", "net asset", "percentage",
            }

            for m in self.RE_SECTOR_LINE_GENERIC.finditer(search_text):
                sector = m.group("sector").strip()
                if any(kw in sector.lower() for kw in noise_words):
                    continue
                pct = self._safe_float(m.group("pct"))
                if pct is None or pct <= 0 or pct > 100:
                    continue
                allocations.append(
                    SectorAllocation(sector_name=sector, weight_pct=pct),
                )
            logger.debug(
                "Generic: extracted %d sector allocations", len(allocations),
            )
        except Exception:
            logger.exception("Generic: error extracting sector allocation")

        if not allocations:
            return super().extract_sector_allocation(text)
        return allocations

    def extract_aum(self, text: str) -> Optional[float]:
        """Extract AUM with broad label matching."""
        logger.debug("Generic: extracting AUM")
        try:
            m = self.RE_AUM_GENERIC.search(text)
            if m:
                val = self._safe_float(m.group("value"))
                logger.debug("Generic: AUM: %s Cr", val)
                return val
        except Exception:
            logger.exception("Generic: error extracting AUM")
        return super().extract_aum(text)

    def extract_nav(self, text: str) -> tuple[Optional[float], Optional[date]]:
        """Extract NAV with broader pattern."""
        logger.debug("Generic: extracting NAV")
        try:
            nav_value: Optional[float] = None
            nav_date: Optional[date] = None

            m_val = self.RE_NAV_GENERIC.search(text)
            if m_val:
                nav_value = self._safe_float(m_val.group("value"))

            m_date = self.RE_NAV_DATE.search(text)
            if m_date:
                nav_date = self._parse_date(m_date.group("date"))

            if nav_value is not None:
                logger.debug("Generic: NAV=%s  date=%s", nav_value, nav_date)
                return nav_value, nav_date
        except Exception:
            logger.exception("Generic: error extracting NAV")
        return super().extract_nav(text)

    def extract_expense_ratio(self, text: str) -> Optional[float]:
        """Extract expense ratio, also matching 'TER' abbreviation."""
        logger.debug("Generic: extracting expense ratio")
        try:
            m = self.RE_EXPENSE_GENERIC.search(text)
            if m:
                val = self._safe_float(m.group("value"))
                logger.debug("Generic: expense ratio: %s%%", val)
                return val
        except Exception:
            logger.exception("Generic: error extracting expense ratio")
        return super().extract_expense_ratio(text)

    def extract_benchmark(self, text: str) -> Optional[str]:
        """Extract benchmark with broader label matching."""
        logger.debug("Generic: extracting benchmark")
        try:
            m = self.RE_BENCHMARK_GENERIC.search(text)
            if m:
                name = m.group("name").strip().rstrip(".")
                logger.debug("Generic: benchmark: %s", name)
                return name
        except Exception:
            logger.exception("Generic: error extracting benchmark")
        return super().extract_benchmark(text)

    def extract_scheme_category(self, text: str) -> Optional[str]:
        """Extract scheme category with widened label matching."""
        logger.debug("Generic: extracting scheme category")
        try:
            m = self.RE_CATEGORY_GENERIC.search(text)
            if m:
                cat = m.group("cat").strip()
                logger.debug("Generic: scheme category: %s", cat)
                return cat
        except Exception:
            logger.exception("Generic: error extracting scheme category")
        return super().extract_scheme_category(text)
