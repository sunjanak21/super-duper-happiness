"""Base AMC parser for mutual fund factsheet PDFs.

Uses PyMuPDF (fitz) as the primary text extraction engine and
pdfplumber as a fallback for table-heavy pages.
"""

import logging
import re
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import pdfplumber

from models.schema import (
    FundManagerInfo,
    Holding,
    SchemeFactsheet,
    SectorAllocation,
)

logger = logging.getLogger(__name__)


class BaseAMCParser(ABC):
    """Abstract base class for AMC-specific factsheet parsers.

    Subclasses should override individual ``extract_*`` methods only where
    the AMC's layout differs materially from the generic regex patterns
    defined here.
    """

    # ------------------------------------------------------------------
    # Regex patterns (class-level constants)
    # ------------------------------------------------------------------

    # Scheme boundary detection
    RE_SCHEME_HEADING = re.compile(
        r"^(?P<name>[A-Z][A-Za-z &\-/()]+(?:Fund|Scheme|Plan|Portfolio))\s*$",
        re.MULTILINE,
    )

    # Fund manager patterns
    RE_FUND_MANAGER = re.compile(
        r"Fund\s*Manager\s*[:\-]?\s*(?P<name>[A-Za-z .]+?)(?:\s*\(|$|\n)",
        re.IGNORECASE,
    )
    RE_MANAGING_SINCE = re.compile(
        r"Managing\s*(?:since|from)\s*[:\-]?\s*(?P<date>[A-Za-z]+[\s,]*\d{4})",
        re.IGNORECASE,
    )
    RE_EXPERIENCE = re.compile(
        r"(?:Experience|Exp)\s*[:\-]?\s*(?P<years>[\d.]+)\s*(?:years|yrs)",
        re.IGNORECASE,
    )

    # Portfolio turnover
    RE_TURNOVER = re.compile(
        r"Portfolio\s*Turnover\s*(?:Ratio)?\s*[:\-]?\s*(?P<value>[\d.]+)\s*%?",
        re.IGNORECASE,
    )

    # AUM
    RE_AUM = re.compile(
        r"(?:AUM|Assets?\s*Under\s*Management|Net\s*Assets|Fund\s*Size)"
        r"\s*[:\-]?\s*(?:Rs\.?\s*|INR\s*)?(?P<value>[\d,]+(?:\.\d+)?)"
        r"\s*(?:Cr(?:ores?)?|Crore)",
        re.IGNORECASE,
    )

    # NAV
    RE_NAV = re.compile(
        r"NAV\s*[:\-]?\s*(?:Rs\.?\s*|INR\s*)?(?P<value>[\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    RE_NAV_DATE = re.compile(
        r"NAV\s*(?:as\s*on|Date)\s*[:\-]?\s*(?P<date>[\d]{1,2}[\s\-/]+[A-Za-z]+[\s\-/,]+\d{4})",
        re.IGNORECASE,
    )

    # Expense ratio
    RE_EXPENSE_RATIO = re.compile(
        r"(?:Total\s*)?Expense\s*Ratio\s*[:\-]?\s*(?P<value>[\d.]+)\s*%",
        re.IGNORECASE,
    )

    # Benchmark
    RE_BENCHMARK = re.compile(
        r"Benchmark\s*[:\-]?\s*(?P<name>[A-Za-z0-9 &\-/()]+?)(?:\n|$)",
        re.IGNORECASE,
    )

    # Inception date
    RE_INCEPTION = re.compile(
        r"(?:Inception\s*Date|Date\s*of\s*Inception|Allotment\s*Date)"
        r"\s*[:\-]?\s*(?P<date>[\d]{1,2}[\s\-/]+[A-Za-z]+[\s\-/,]+\d{4})",
        re.IGNORECASE,
    )

    # PE / PB
    RE_PE = re.compile(
        r"(?:Portfolio\s*)?P/?E\s*(?:Ratio)?\s*[:\-]?\s*(?P<value>[\d.]+)",
        re.IGNORECASE,
    )
    RE_PB = re.compile(
        r"(?:Portfolio\s*)?P/?B\s*(?:Ratio)?\s*[:\-]?\s*(?P<value>[\d.]+)",
        re.IGNORECASE,
    )

    # Scheme category
    RE_CATEGORY = re.compile(
        r"(?:Category|Scheme\s*Type|Fund\s*Type)\s*[:\-]?\s*(?P<cat>[A-Za-z ()]+)",
        re.IGNORECASE,
    )

    # Sector allocation
    RE_SECTOR_LINE = re.compile(
        r"(?P<sector>[A-Za-z &/\-]+?)\s+(?P<pct>[\d.]+)\s*%",
    )

    # Holdings table row (generic)
    RE_HOLDING_ROW = re.compile(
        r"(?P<name>[A-Za-z .'&]+?)\s{2,}(?P<sector>[A-Za-z &/\-]+?)?\s{2,}"
        r"(?P<pct>[\d.]+)\s*%?",
    )

    # Expected number of fields for confidence scoring
    FIELDS_EXPECTED = 12

    # ------------------------------------------------------------------
    # PDF text helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text_fitz(pdf_path: str) -> dict[int, str]:
        """Return {page_number: text} using PyMuPDF."""
        pages: dict[int, str] = {}
        try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                pages[page_num] = doc[page_num].get_text()
            doc.close()
        except Exception:
            logger.exception("PyMuPDF failed to read %s", pdf_path)
        return pages

    @staticmethod
    def _extract_text_pdfplumber(pdf_path: str) -> dict[int, str]:
        """Return {page_number: text} using pdfplumber (fallback)."""
        pages: dict[int, str] = {}
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    pages[i] = text
        except Exception:
            logger.exception("pdfplumber failed to read %s", pdf_path)
        return pages

    @staticmethod
    def _extract_tables_pdfplumber(
        pdf_path: str, page_numbers: list[int] | None = None,
    ) -> dict[int, list[list[list[str | None]]]]:
        """Return {page_number: list_of_tables} via pdfplumber.

        Each table is a list of rows, each row a list of cell strings.
        """
        result: dict[int, list[list[list[str | None]]]] = {}
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    if page_numbers is not None and i not in page_numbers:
                        continue
                    tables = page.extract_tables() or []
                    if tables:
                        result[i] = tables
        except Exception:
            logger.exception("pdfplumber table extraction failed for %s", pdf_path)
        return result

    def _full_text(self, pages: dict[int, str]) -> str:
        """Concatenate all pages into a single string."""
        return "\n".join(pages.get(i, "") for i in sorted(pages))

    # ------------------------------------------------------------------
    # Date parsing helper
    # ------------------------------------------------------------------

    _DATE_FORMATS = [
        "%d %B %Y",
        "%d %b %Y",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d/%m/%Y",
        "%d %B, %Y",
        "%d %b, %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]

    @classmethod
    def _parse_date(cls, raw: str) -> Optional[date]:
        """Try multiple date formats; return None on failure."""
        cleaned = raw.strip().replace(",", ", ").replace("  ", " ")
        for fmt in cls._DATE_FORMATS:
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
        # Retry with comma stripped entirely
        cleaned_no_comma = raw.strip().replace(",", "")
        for fmt in cls._DATE_FORMATS:
            try:
                return datetime.strptime(cleaned_no_comma, fmt).date()
            except ValueError:
                continue
        logger.debug("Could not parse date string: %r", raw)
        return None

    @classmethod
    def _safe_float(cls, raw: str) -> Optional[float]:
        """Parse a float from a string, stripping commas."""
        try:
            return float(raw.replace(",", "").strip())
        except (ValueError, AttributeError):
            return None

    # ------------------------------------------------------------------
    # Abstract / overridable extraction methods
    # ------------------------------------------------------------------

    def detect_scheme_boundaries(
        self, pdf_path: str,
    ) -> dict[str, tuple[int, int]]:
        """Detect where each scheme starts/ends in a multi-scheme PDF.

        Returns a mapping of scheme_name -> (start_page, end_page).
        """
        logger.debug("Detecting scheme boundaries in %s", pdf_path)
        pages = self._extract_text_fitz(pdf_path)
        boundaries: dict[str, tuple[int, int]] = {}
        found: list[tuple[str, int]] = []

        for page_num in sorted(pages):
            text = pages[page_num]
            for m in self.RE_SCHEME_HEADING.finditer(text):
                name = m.group("name").strip()
                found.append((name, page_num))
                logger.debug(
                    "Found scheme heading %r on page %d", name, page_num,
                )

        total_pages = max(pages) if pages else 0
        for idx, (name, start) in enumerate(found):
            end = found[idx + 1][1] - 1 if idx + 1 < len(found) else total_pages
            boundaries[name] = (start, end)

        if not boundaries and pages:
            # Single-scheme PDF: treat entire document as one scheme
            logger.debug("No scheme headings found; treating as single-scheme PDF")
            first_page_text = pages.get(0, "")
            # Use first non-empty line as scheme name
            for line in first_page_text.splitlines():
                stripped = line.strip()
                if stripped and len(stripped) > 5:
                    boundaries[stripped] = (0, total_pages)
                    break

        return boundaries

    def extract_fund_managers(self, text: str) -> list[FundManagerInfo]:
        """Extract fund manager names and metadata from text."""
        logger.debug("Extracting fund managers")
        managers: list[FundManagerInfo] = []
        try:
            matches = self.RE_FUND_MANAGER.findall(text)
            for name_str in matches:
                name_str = name_str.strip()
                if not name_str or len(name_str) < 3:
                    continue
                info = FundManagerInfo(name=name_str)

                # Look for managing-since near the name
                region_start = text.find(name_str)
                region = text[region_start : region_start + 300] if region_start >= 0 else ""

                since_m = self.RE_MANAGING_SINCE.search(region)
                if since_m:
                    info.managing_since = self._parse_date(since_m.group("date"))

                exp_m = self.RE_EXPERIENCE.search(region)
                if exp_m:
                    info.experience_years = self._safe_float(exp_m.group("years"))

                managers.append(info)
                logger.debug("Found fund manager: %s", info.name)
        except Exception:
            logger.exception("Error extracting fund managers")
        return managers

    def extract_portfolio_turnover(self, text: str) -> Optional[float]:
        """Extract portfolio turnover ratio (percentage)."""
        logger.debug("Extracting portfolio turnover")
        try:
            m = self.RE_TURNOVER.search(text)
            if m:
                val = self._safe_float(m.group("value"))
                logger.debug("Portfolio turnover: %s", val)
                return val
        except Exception:
            logger.exception("Error extracting portfolio turnover")
        return None

    def extract_holdings(
        self, text: str, pages: dict[int, str], pdf_path: str | None = None,
    ) -> list[Holding]:
        """Extract top holdings from the factsheet.

        Uses pdfplumber table extraction when *pdf_path* is provided,
        otherwise falls back to regex on the raw text.
        """
        logger.debug("Extracting holdings")
        holdings: list[Holding] = []
        try:
            # Attempt pdfplumber table extraction first
            if pdf_path:
                tables_by_page = self._extract_tables_pdfplumber(pdf_path)
                for _page_num, tables in tables_by_page.items():
                    for table in tables:
                        holdings.extend(self._parse_holdings_table(table))
                if holdings:
                    logger.debug(
                        "Extracted %d holdings via pdfplumber tables", len(holdings),
                    )
                    return holdings

            # Fallback: regex on concatenated text
            full = text if isinstance(text, str) else self._full_text(pages)
            for m in self.RE_HOLDING_ROW.finditer(full):
                name = m.group("name").strip()
                pct = self._safe_float(m.group("pct"))
                if pct is None or pct <= 0 or pct > 100:
                    continue
                sector = m.group("sector")
                sector = sector.strip() if sector else None
                holdings.append(
                    Holding(stock_name=name, sector=sector, weight_pct=pct),
                )
            logger.debug("Extracted %d holdings via regex", len(holdings))
        except Exception:
            logger.exception("Error extracting holdings")
        return holdings

    def _parse_holdings_table(
        self, table: list[list[str | None]],
    ) -> list[Holding]:
        """Parse a pdfplumber table into Holding objects.

        Tries to detect which columns correspond to name, sector, and weight.
        """
        holdings: list[Holding] = []
        if not table or len(table) < 2:
            return holdings

        header = [str(c).lower().strip() if c else "" for c in table[0]]

        # Identify column indices
        name_idx: int | None = None
        sector_idx: int | None = None
        weight_idx: int | None = None
        for i, col in enumerate(header):
            if any(kw in col for kw in ("company", "stock", "name", "issuer", "instrument", "holding")):
                name_idx = i
            elif any(kw in col for kw in ("sector", "industry")):
                sector_idx = i
            elif any(kw in col for kw in ("% of", "weight", "net asset", "nav", "allocation")):
                weight_idx = i

        if name_idx is None or weight_idx is None:
            return holdings

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
                    sector = str(row[sector_idx]).strip()
                holdings.append(
                    Holding(stock_name=name, sector=sector, weight_pct=pct),
                )
            except (IndexError, TypeError):
                continue
        return holdings

    def extract_sector_allocation(self, text: str) -> list[SectorAllocation]:
        """Extract sector allocation from text."""
        logger.debug("Extracting sector allocation")
        allocations: list[SectorAllocation] = []
        try:
            # Look for sector allocation section
            section_pattern = re.compile(
                r"(?:Sector\s*Allocation|Industry\s*Allocation|Sector\s*Wise)"
                r".*?(?=\n\s*\n|\Z)",
                re.IGNORECASE | re.DOTALL,
            )
            section_m = section_pattern.search(text)
            search_text = section_m.group(0) if section_m else text

            for m in self.RE_SECTOR_LINE.finditer(search_text):
                sector = m.group("sector").strip()
                pct = self._safe_float(m.group("pct"))
                if pct is None or pct <= 0 or pct > 100:
                    continue
                # Skip headers / noise
                if any(kw in sector.lower() for kw in ("sector", "total", "allocation", "industry")):
                    continue
                allocations.append(
                    SectorAllocation(sector_name=sector, weight_pct=pct),
                )
            logger.debug("Extracted %d sector allocations", len(allocations))
        except Exception:
            logger.exception("Error extracting sector allocation")
        return allocations

    def extract_pe_pb(self, text: str) -> tuple[Optional[float], Optional[float]]:
        """Extract portfolio P/E and P/B ratios."""
        logger.debug("Extracting P/E and P/B")
        pe: Optional[float] = None
        pb: Optional[float] = None
        try:
            m_pe = self.RE_PE.search(text)
            if m_pe:
                pe = self._safe_float(m_pe.group("value"))
            m_pb = self.RE_PB.search(text)
            if m_pb:
                pb = self._safe_float(m_pb.group("value"))
            logger.debug("P/E=%s  P/B=%s", pe, pb)
        except Exception:
            logger.exception("Error extracting P/E and P/B")
        return pe, pb

    def extract_aum(self, text: str) -> Optional[float]:
        """Extract AUM in crores."""
        logger.debug("Extracting AUM")
        try:
            m = self.RE_AUM.search(text)
            if m:
                val = self._safe_float(m.group("value"))
                logger.debug("AUM: %s Cr", val)
                return val
        except Exception:
            logger.exception("Error extracting AUM")
        return None

    def extract_nav(self, text: str) -> tuple[Optional[float], Optional[date]]:
        """Extract NAV value and date."""
        logger.debug("Extracting NAV")
        nav_value: Optional[float] = None
        nav_date: Optional[date] = None
        try:
            m_val = self.RE_NAV.search(text)
            if m_val:
                nav_value = self._safe_float(m_val.group("value"))

            m_date = self.RE_NAV_DATE.search(text)
            if m_date:
                nav_date = self._parse_date(m_date.group("date"))
            logger.debug("NAV=%s  date=%s", nav_value, nav_date)
        except Exception:
            logger.exception("Error extracting NAV")
        return nav_value, nav_date

    def extract_expense_ratio(self, text: str) -> Optional[float]:
        """Extract the total expense ratio (percentage)."""
        logger.debug("Extracting expense ratio")
        try:
            m = self.RE_EXPENSE_RATIO.search(text)
            if m:
                val = self._safe_float(m.group("value"))
                logger.debug("Expense ratio: %s%%", val)
                return val
        except Exception:
            logger.exception("Error extracting expense ratio")
        return None

    def extract_benchmark(self, text: str) -> Optional[str]:
        """Extract the benchmark index name."""
        logger.debug("Extracting benchmark")
        try:
            m = self.RE_BENCHMARK.search(text)
            if m:
                name = m.group("name").strip()
                logger.debug("Benchmark: %s", name)
                return name
        except Exception:
            logger.exception("Error extracting benchmark")
        return None

    def extract_inception_date(self, text: str) -> Optional[date]:
        """Extract the scheme inception / allotment date."""
        logger.debug("Extracting inception date")
        try:
            m = self.RE_INCEPTION.search(text)
            if m:
                d = self._parse_date(m.group("date"))
                logger.debug("Inception date: %s", d)
                return d
        except Exception:
            logger.exception("Error extracting inception date")
        return None

    def extract_scheme_category(self, text: str) -> Optional[str]:
        """Extract the SEBI / internal scheme category label."""
        logger.debug("Extracting scheme category")
        try:
            m = self.RE_CATEGORY.search(text)
            if m:
                cat = m.group("cat").strip()
                logger.debug("Scheme category: %s", cat)
                return cat
        except Exception:
            logger.exception("Error extracting scheme category")
        return None

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _confidence(factsheet: SchemeFactsheet) -> float:
        """Compute parse_confidence = fields_found / fields_expected."""
        fields_expected = BaseAMCParser.FIELDS_EXPECTED
        found = 0
        if factsheet.fund_managers:
            found += 1
        if factsheet.portfolio_turnover_pct is not None:
            found += 1
        if factsheet.aum_cr is not None:
            found += 1
        if factsheet.expense_ratio_pct is not None:
            found += 1
        if factsheet.top_holdings:
            found += 1
        if factsheet.sector_allocation:
            found += 1
        if factsheet.portfolio_pe is not None:
            found += 1
        if factsheet.portfolio_pb is not None:
            found += 1
        if factsheet.nav is not None:
            found += 1
        if factsheet.benchmark is not None:
            found += 1
        if factsheet.inception_date is not None:
            found += 1
        if factsheet.scheme_category is not None:
            found += 1
        return round(found / fields_expected, 4)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def parse_factsheet(
        self,
        pdf_path: str,
        amc_name: str,
        factsheet_date: date,
    ) -> list[SchemeFactsheet]:
        """Parse a factsheet PDF and return one SchemeFactsheet per scheme.

        Never raises; returns an empty list on total failure.
        """
        logger.info("Parsing factsheet: %s", pdf_path)
        results: list[SchemeFactsheet] = []

        try:
            pages = self._extract_text_fitz(pdf_path)
            if not pages:
                logger.warning("No text extracted from %s; trying pdfplumber", pdf_path)
                pages = self._extract_text_pdfplumber(pdf_path)
            if not pages:
                logger.error("Could not extract any text from %s", pdf_path)
                return results

            boundaries = self.detect_scheme_boundaries(pdf_path)
            if not boundaries:
                logger.warning("No scheme boundaries detected; treating full PDF as one scheme")
                boundaries = {"Unknown Scheme": (0, max(pages))}

            for scheme_name, (start, end) in boundaries.items():
                logger.debug(
                    "Processing scheme %r (pages %d-%d)", scheme_name, start, end,
                )
                scheme_pages = {
                    p: pages[p] for p in range(start, end + 1) if p in pages
                }
                scheme_text = self._full_text(scheme_pages)

                fund_managers = self.extract_fund_managers(scheme_text)
                turnover = self.extract_portfolio_turnover(scheme_text)
                holdings = self.extract_holdings(
                    scheme_text, scheme_pages, pdf_path=pdf_path,
                )
                sector_alloc = self.extract_sector_allocation(scheme_text)
                pe, pb = self.extract_pe_pb(scheme_text)
                aum = self.extract_aum(scheme_text)
                nav_val, nav_dt = self.extract_nav(scheme_text)
                expense = self.extract_expense_ratio(scheme_text)
                benchmark = self.extract_benchmark(scheme_text)
                inception = self.extract_inception_date(scheme_text)
                category = self.extract_scheme_category(scheme_text)

                warnings: list[str] = []
                if not holdings:
                    warnings.append("No holdings extracted")
                if not fund_managers:
                    warnings.append("No fund manager info found")
                if aum is None:
                    warnings.append("AUM not found")

                factsheet = SchemeFactsheet(
                    amc_name=amc_name,
                    scheme_name=scheme_name,
                    scheme_category=category,
                    factsheet_date=factsheet_date,
                    fund_managers=fund_managers,
                    portfolio_turnover_pct=turnover,
                    aum_cr=aum,
                    expense_ratio_pct=expense,
                    top_holdings=holdings,
                    total_holdings_count=len(holdings) if holdings else None,
                    sector_allocation=sector_alloc,
                    portfolio_pe=pe,
                    portfolio_pb=pb,
                    nav=nav_val,
                    nav_date=nav_dt,
                    benchmark=benchmark,
                    inception_date=inception,
                    source_pdf_path=pdf_path,
                    parse_warnings=warnings,
                )
                factsheet.parse_confidence = self._confidence(factsheet)
                results.append(factsheet)
                logger.info(
                    "Scheme %r parsed (confidence=%.2f)",
                    scheme_name,
                    factsheet.parse_confidence,
                )

        except Exception:
            logger.exception("Fatal error parsing %s", pdf_path)

        return results
