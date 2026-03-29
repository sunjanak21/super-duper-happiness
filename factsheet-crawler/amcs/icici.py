"""ICICI Prudential Mutual Fund factsheet parser.

ICICI Prudential factsheets typically feature:
- Holdings in a different table layout (sometimes without grid lines)
- Fund manager info labeled "Fund Manager(s)"
- Portfolio turnover reported as a decimal (e.g. 0.85) instead of percentage
"""

import logging
import re
from typing import Optional

from models.schema import FundManagerInfo

from .base import BaseAMCParser

logger = logging.getLogger(__name__)


class ICICIAMCParser(BaseAMCParser):
    """Parser tuned for ICICI Prudential Mutual Fund factsheet PDFs."""

    # ------------------------------------------------------------------
    # ICICI-specific regex overrides
    # ------------------------------------------------------------------

    # ICICI labels section as "Fund Manager(s)" with parenthesised plural
    RE_FUND_MANAGER_ICICI = re.compile(
        r"Fund\s*Manager\(?s?\)?\s*[:\-]?\s*"
        r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
        re.IGNORECASE,
    )

    # ICICI sometimes lists multiple managers separated by " & " or ","
    RE_FUND_MANAGER_MULTI = re.compile(
        r"Fund\s*Manager\(?s?\)?\s*[:\-]?\s*"
        r"(?P<names>[A-Za-z .&,]+?)(?:\n|Benchmark|Inception|Category)",
        re.IGNORECASE,
    )

    # ICICI turnover may appear as a decimal (e.g. "0.85" meaning 85%)
    RE_TURNOVER_ICICI = re.compile(
        r"Portfolio\s*Turnover\s*(?:Ratio)?\s*[:\-]?\s*(?P<value>[\d.]+)\s*%?\s*"
        r"(?:times?)?",
        re.IGNORECASE,
    )

    # ICICI holdings row: often "Stock Name   8.52" (no explicit % sign)
    RE_HOLDING_ROW_ICICI = re.compile(
        r"(?P<name>[A-Za-z .'&()]+?)\s{2,}"
        r"(?P<pct>[\d]+\.[\d]{1,2})\s*%?",
    )

    # ------------------------------------------------------------------
    # Overridden methods
    # ------------------------------------------------------------------

    def extract_fund_managers(self, text: str) -> list[FundManagerInfo]:
        """Extract fund managers from ICICI's 'Fund Manager(s)' label.

        Handles both single and multiple manager listings.
        """
        logger.debug("ICICI: extracting fund managers")
        managers: list[FundManagerInfo] = []
        seen_names: set[str] = set()

        try:
            # Try multi-manager pattern first
            multi_m = self.RE_FUND_MANAGER_MULTI.search(text)
            if multi_m:
                raw_names = multi_m.group("names")
                # Split on "&", "," or "and"
                parts = re.split(r"\s*[&,]\s*|\s+and\s+", raw_names)
                for part in parts:
                    name = part.strip().rstrip(".")
                    if not name or len(name) < 3:
                        continue
                    # Skip noise words
                    if name.lower() in ("mr", "ms", "shri", "dr"):
                        continue
                    # Strip honorifics
                    name = re.sub(
                        r"^(?:Mr\.?\s*|Ms\.?\s*|Shri\.?\s*|Dr\.?\s*)",
                        "",
                        name,
                    ).strip()
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)
                    managers.append(FundManagerInfo(name=name))
                    logger.debug("ICICI: found fund manager %s", name)

            # Also try single-match pattern for any missed managers
            for m in self.RE_FUND_MANAGER_ICICI.finditer(text):
                name = m.group("name").strip()
                if not name or len(name) < 3 or name in seen_names:
                    continue
                seen_names.add(name)

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
                logger.debug("ICICI: found fund manager %s", info.name)

        except Exception:
            logger.exception("ICICI: error extracting fund managers")

        if not managers:
            logger.debug("ICICI patterns found no managers; trying base")
            return super().extract_fund_managers(text)
        return managers

    def extract_portfolio_turnover(self, text: str) -> Optional[float]:
        """Extract portfolio turnover, handling ICICI's decimal format.

        ICICI sometimes reports turnover as a decimal ratio (e.g. 0.85
        meaning 85%) rather than a percentage.  Values <= 5.0 without a
        percent sign are treated as ratios and converted to percentages.
        """
        logger.debug("ICICI: extracting portfolio turnover")
        try:
            m = self.RE_TURNOVER_ICICI.search(text)
            if m:
                raw = m.group("value")
                val = self._safe_float(raw)
                if val is None:
                    return None

                # Detect decimal ratio: if value < 5 and there is no % sign
                # right after the number, treat it as a ratio
                full_match = m.group(0)
                has_pct = "%" in full_match
                if val <= 5.0 and not has_pct:
                    val = round(val * 100, 2)
                    logger.debug(
                        "ICICI: converted turnover ratio %.4f -> %.2f%%",
                        self._safe_float(raw),
                        val,
                    )
                else:
                    logger.debug("ICICI: portfolio turnover: %s%%", val)
                return val
        except Exception:
            logger.exception("ICICI: error extracting portfolio turnover")

        return super().extract_portfolio_turnover(text)
