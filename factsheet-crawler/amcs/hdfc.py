"""HDFC Mutual Fund factsheet parser.

HDFC factsheets typically feature:
- Scheme names in large bold font (often ALL-CAPS or title-case)
- Holdings tables with columns: Company | Sector/Industry | % of Net Assets
- Fund manager information in a sidebar panel
"""

import logging
import re
from typing import Optional

from models.schema import FundManagerInfo, Holding

from .base import BaseAMCParser

logger = logging.getLogger(__name__)


class HDFCAMCParser(BaseAMCParser):
    """Parser tuned for HDFC Mutual Fund factsheet PDFs."""

    # ------------------------------------------------------------------
    # HDFC-specific regex overrides
    # ------------------------------------------------------------------

    # HDFC uses prominent bold headings, often ALL-CAPS with "FUND" suffix
    RE_SCHEME_HEADING = re.compile(
        r"^(?P<name>HDFC\s+[A-Z][A-Za-z &\-/()]+?"
        r"(?:Fund|Scheme|Plan|Portfolio|Advantage|Savings|Opportunities))"
        r"\s*$",
        re.MULTILINE,
    )

    # HDFC labels fund managers in sidebar: "Fund Manager : Mr. Prashant Jain"
    RE_FUND_MANAGER_HDFC = re.compile(
        r"Fund\s*Manager\s*[:\-]?\s*(?:Mr\.?\s*|Ms\.?\s*|Shri\.?\s*)?"
        r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
        re.IGNORECASE,
    )

    # HDFC holdings rows: "ICICI Bank Ltd.   Financial Services   8.52%"
    RE_HOLDING_ROW_HDFC = re.compile(
        r"(?P<name>[A-Za-z .'&()]+?)\s{2,}"
        r"(?P<sector>[A-Za-z &/\-]+?)\s{2,}"
        r"(?P<pct>[\d]+\.[\d]{1,2})\s*%?",
    )

    # ------------------------------------------------------------------
    # Overridden methods
    # ------------------------------------------------------------------

    def detect_scheme_boundaries(
        self, pdf_path: str,
    ) -> dict[str, tuple[int, int]]:
        """Detect scheme boundaries using HDFC-specific heading patterns.

        HDFC factsheets place each scheme on a new page with the scheme
        name in a large bold header.  Falls back to the base implementation
        when the HDFC pattern finds nothing.
        """
        logger.debug("HDFC: detecting scheme boundaries in %s", pdf_path)
        pages = self._extract_text_fitz(pdf_path)
        if not pages:
            pages = self._extract_text_pdfplumber(pdf_path)
        if not pages:
            return {}

        found: list[tuple[str, int]] = []
        for page_num in sorted(pages):
            text = pages[page_num]
            for m in self.RE_SCHEME_HEADING.finditer(text):
                name = m.group("name").strip()
                found.append((name, page_num))
                logger.debug(
                    "HDFC: found scheme heading %r on page %d", name, page_num,
                )

        if not found:
            logger.debug(
                "HDFC heading pattern found nothing; falling back to base",
            )
            return super().detect_scheme_boundaries(pdf_path)

        boundaries: dict[str, tuple[int, int]] = {}
        total_pages = max(pages)
        for idx, (name, start) in enumerate(found):
            end = found[idx + 1][1] - 1 if idx + 1 < len(found) else total_pages
            boundaries[name] = (start, end)
        return boundaries

    def extract_fund_managers(self, text: str) -> list[FundManagerInfo]:
        """Extract fund managers using HDFC sidebar format.

        HDFC frequently uses "Mr./Ms." prefixes and places manager info
        in a sidebar.  Falls back to the base pattern if nothing matches.
        """
        logger.debug("HDFC: extracting fund managers")
        managers: list[FundManagerInfo] = []
        try:
            seen_names: set[str] = set()
            for m in self.RE_FUND_MANAGER_HDFC.finditer(text):
                name = m.group("name").strip()
                if not name or len(name) < 3 or name in seen_names:
                    continue
                seen_names.add(name)

                info = FundManagerInfo(name=name)

                # Search nearby text for managing-since / experience
                region_start = text.find(name)
                region = text[region_start: region_start + 400] if region_start >= 0 else ""

                since_m = self.RE_MANAGING_SINCE.search(region)
                if since_m:
                    info.managing_since = self._parse_date(since_m.group("date"))

                exp_m = self.RE_EXPERIENCE.search(region)
                if exp_m:
                    info.experience_years = self._safe_float(exp_m.group("years"))

                managers.append(info)
                logger.debug("HDFC: found fund manager %s", info.name)

        except Exception:
            logger.exception("HDFC: error extracting fund managers")

        if not managers:
            logger.debug("HDFC-specific pattern found no managers; trying base")
            return super().extract_fund_managers(text)
        return managers

    def extract_holdings(
        self,
        text: str,
        pages: dict[int, str],
        pdf_path: str | None = None,
    ) -> list[Holding]:
        """Extract holdings using HDFC table format.

        HDFC uses columns: Company | Sector/Industry | % of Net Assets.
        Attempts pdfplumber table extraction first, then the HDFC-specific
        regex, and finally falls back to the base implementation.
        """
        logger.debug("HDFC: extracting holdings")
        holdings: list[Holding] = []

        # 1. Try pdfplumber tables (best accuracy for HDFC grids)
        if pdf_path:
            try:
                tables_by_page = self._extract_tables_pdfplumber(pdf_path)
                for _page_num, tables in tables_by_page.items():
                    for table in tables:
                        holdings.extend(self._parse_holdings_table(table))
                if holdings:
                    logger.debug(
                        "HDFC: extracted %d holdings via pdfplumber", len(holdings),
                    )
                    return holdings
            except Exception:
                logger.exception("HDFC: pdfplumber table extraction failed")

        # 2. HDFC-specific regex
        try:
            full = text if isinstance(text, str) else self._full_text(pages)
            for m in self.RE_HOLDING_ROW_HDFC.finditer(full):
                name = m.group("name").strip()
                pct = self._safe_float(m.group("pct"))
                if pct is None or pct <= 0 or pct > 100:
                    continue
                sector = m.group("sector").strip() if m.group("sector") else None
                holdings.append(
                    Holding(stock_name=name, sector=sector, weight_pct=pct),
                )
            if holdings:
                logger.debug(
                    "HDFC: extracted %d holdings via HDFC regex", len(holdings),
                )
                return holdings
        except Exception:
            logger.exception("HDFC: regex extraction failed")

        # 3. Fall back to base
        logger.debug("HDFC: falling back to base holdings extraction")
        return super().extract_holdings(text, pages, pdf_path=pdf_path)
