"""SBI Mutual Fund factsheet parser.

SBI MF factsheets typically feature:
- Holdings table in a slightly different format (Name | Rating/Industry | % to NAV)
- Category labels using SBI-specific terminology (e.g. "Equity - Large Cap"
  rather than SEBI standard "Large Cap Fund")
"""

import logging
import re
from typing import Optional

from models.schema import Holding

from .base import BaseAMCParser

logger = logging.getLogger(__name__)


class SBIAMCParser(BaseAMCParser):
    """Parser tuned for SBI Mutual Fund factsheet PDFs."""

    # ------------------------------------------------------------------
    # SBI-specific regex overrides
    # ------------------------------------------------------------------

    # SBI often uses "% to NAV" or "% to Net Assets" as the weight column
    RE_HOLDING_ROW_SBI = re.compile(
        r"(?P<name>[A-Za-z .'&()]+?)\s{2,}"
        r"(?:(?P<rating_or_sector>[A-Za-z &/\-]+?)\s{2,})?"
        r"(?P<pct>[\d]+\.[\d]{1,2})\s*%?",
    )

    # SBI category labels: "Equity - Large Cap", "Debt - Short Duration",
    # "Hybrid - Aggressive"
    RE_CATEGORY_SBI = re.compile(
        r"(?:Category|Scheme\s*Type|Fund\s*Type|Scheme\s*Category)"
        r"\s*[:\-]?\s*"
        r"(?P<cat>(?:Equity|Debt|Hybrid|Solution Oriented|Other)"
        r"\s*[\-–]\s*[A-Za-z ()]+)",
        re.IGNORECASE,
    )

    # Fallback: broader SBI category that might just be a plain label
    RE_CATEGORY_SBI_BROAD = re.compile(
        r"(?:Category|Type of Scheme|Scheme\s*Category)"
        r"\s*[:\-]?\s*(?P<cat>[A-Za-z ()\-–]+?)(?:\n|$)",
        re.IGNORECASE,
    )

    # SBI sometimes labels AUM as "Fund Size" or "Corpus"
    RE_AUM_SBI = re.compile(
        r"(?:AUM|Fund\s*Size|Net\s*Assets|Corpus)"
        r"\s*[:\-]?\s*(?:Rs\.?\s*|INR\s*|`\s*)?"
        r"(?P<value>[\d,]+(?:\.\d+)?)\s*"
        r"(?:Cr(?:ores?)?|Crore|Lakhs?\s*Crore)",
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Overridden methods
    # ------------------------------------------------------------------

    def extract_holdings(
        self,
        text: str,
        pages: dict[int, str],
        pdf_path: str | None = None,
    ) -> list[Holding]:
        """Extract holdings using SBI's table layout.

        SBI tables often include a rating/industry column that may be
        absent for equity holdings.  Tries pdfplumber first, then the
        SBI-specific regex, and finally falls back to the base.
        """
        logger.debug("SBI: extracting holdings")
        holdings: list[Holding] = []

        # 1. pdfplumber tables
        if pdf_path:
            try:
                tables_by_page = self._extract_tables_pdfplumber(pdf_path)
                for _page_num, tables in tables_by_page.items():
                    for table in tables:
                        holdings.extend(self._parse_holdings_table_sbi(table))
                if holdings:
                    logger.debug(
                        "SBI: extracted %d holdings via pdfplumber", len(holdings),
                    )
                    return holdings
            except Exception:
                logger.exception("SBI: pdfplumber table extraction failed")

        # 2. SBI regex
        try:
            full = text if isinstance(text, str) else self._full_text(pages)
            for m in self.RE_HOLDING_ROW_SBI.finditer(full):
                name = m.group("name").strip()
                pct = self._safe_float(m.group("pct"))
                if pct is None or pct <= 0 or pct > 100:
                    continue
                sector = None
                rating_or_sector = m.group("rating_or_sector")
                if rating_or_sector:
                    candidate = rating_or_sector.strip()
                    # If it looks like a credit rating, skip it as sector
                    if not re.match(r"^[A-Z]{1,4}[+-]?$", candidate):
                        sector = candidate
                holdings.append(
                    Holding(stock_name=name, sector=sector, weight_pct=pct),
                )
            if holdings:
                logger.debug(
                    "SBI: extracted %d holdings via SBI regex", len(holdings),
                )
                return holdings
        except Exception:
            logger.exception("SBI: regex extraction failed")

        # 3. Fall back to base
        logger.debug("SBI: falling back to base holdings extraction")
        return super().extract_holdings(text, pages, pdf_path=pdf_path)

    def _parse_holdings_table_sbi(
        self, table: list[list[str | None]],
    ) -> list[Holding]:
        """Parse a pdfplumber table using SBI conventions.

        SBI tables may use '% to NAV' or '% to Net Assets' as headers
        and sometimes include a Rating column for debt instruments.
        """
        holdings: list[Holding] = []
        if not table or len(table) < 2:
            return holdings

        header = [str(c).lower().strip() if c else "" for c in table[0]]

        name_idx: int | None = None
        sector_idx: int | None = None
        weight_idx: int | None = None

        for i, col in enumerate(header):
            if any(kw in col for kw in (
                "company", "stock", "name", "issuer", "instrument", "holding", "scrip",
            )):
                name_idx = i
            elif any(kw in col for kw in ("sector", "industry", "rating")):
                sector_idx = i
            elif any(kw in col for kw in (
                "% to nav", "% of nav", "% to net", "% of net", "weight", "allocation",
            )):
                weight_idx = i

        if name_idx is None or weight_idx is None:
            # Try the generic base parser
            return self._parse_holdings_table(table)

        for row in table[1:]:
            try:
                raw_name = row[name_idx] if name_idx < len(row) else None
                raw_weight = row[weight_idx] if weight_idx < len(row) else None
                if not raw_name or not raw_weight:
                    continue
                name = str(raw_name).strip()
                pct = self._safe_float(str(raw_weight).replace("%", ""))
                if not name or pct is None or pct <= 0 or pct > 100:
                    continue
                sector = None
                if sector_idx is not None and sector_idx < len(row) and row[sector_idx]:
                    candidate = str(row[sector_idx]).strip()
                    if not re.match(r"^[A-Z]{1,4}[+-]?$", candidate):
                        sector = candidate
                holdings.append(
                    Holding(stock_name=name, sector=sector, weight_pct=pct),
                )
            except (IndexError, TypeError):
                continue
        return holdings

    def extract_scheme_category(self, text: str) -> Optional[str]:
        """Extract scheme category using SBI's label conventions.

        SBI uses formatted categories like 'Equity - Large Cap' rather
        than bare SEBI labels.
        """
        logger.debug("SBI: extracting scheme category")
        try:
            m = self.RE_CATEGORY_SBI.search(text)
            if m:
                cat = m.group("cat").strip()
                logger.debug("SBI: scheme category: %s", cat)
                return cat

            m = self.RE_CATEGORY_SBI_BROAD.search(text)
            if m:
                cat = m.group("cat").strip()
                logger.debug("SBI: scheme category (broad): %s", cat)
                return cat
        except Exception:
            logger.exception("SBI: error extracting scheme category")

        return super().extract_scheme_category(text)

    def extract_aum(self, text: str) -> Optional[float]:
        """Extract AUM, handling SBI-specific labels like 'Fund Size' or 'Corpus'."""
        logger.debug("SBI: extracting AUM")
        try:
            m = self.RE_AUM_SBI.search(text)
            if m:
                val = self._safe_float(m.group("value"))
                logger.debug("SBI: AUM: %s Cr", val)
                return val
        except Exception:
            logger.exception("SBI: error extracting AUM")

        return super().extract_aum(text)
