"""Normalization layer that standardizes parsed factsheet data."""

import logging
import re
from datetime import date, datetime
from typing import Optional

import yaml

from models.schema import SchemeFactsheet

logger = logging.getLogger(__name__)


def normalize_all(
    schemes: list[SchemeFactsheet], config: dict
) -> list[SchemeFactsheet]:
    """Apply all normalizations to a list of parsed scheme factsheets.

    Args:
        schemes: Raw parsed scheme factsheets.
        config: Pipeline configuration dict (may contain sector_mapping_path, etc.).

    Returns:
        Normalized and deduplicated list of SchemeFactsheet.
    """
    sector_mapping_path = config.get("sector_mapping_path")
    sector_mapping: dict = {}
    if sector_mapping_path:
        sector_mapping = load_sector_mapping(sector_mapping_path)

    normalized: list[SchemeFactsheet] = []
    for scheme in schemes:
        try:
            scheme.scheme_name = normalize_scheme_name(scheme.scheme_name)

            # Normalize sector names in holdings
            for holding in scheme.top_holdings:
                if holding.sector:
                    holding.sector = normalize_sector_name(
                        holding.sector, sector_mapping
                    )

            # Normalize sector allocation names
            for sa in scheme.sector_allocation:
                sa.sector_name = normalize_sector_name(
                    sa.sector_name, sector_mapping
                )

            # Normalize fund manager names
            for fm in scheme.fund_managers:
                fm.name = normalize_fund_manager_name(fm.name)

            normalized.append(scheme)
        except Exception:
            logger.exception(
                "Failed to normalize scheme: %s", scheme.scheme_name
            )

    result = deduplicate(normalized)
    logger.info(
        "Normalization complete: %d input -> %d output schemes",
        len(schemes),
        len(result),
    )
    return result


def normalize_scheme_name(name: str) -> str:
    """Standardize mutual fund scheme names.

    - Strip whitespace
    - Normalize plan variants: "Direct Plan"/"Direct"/"Dir" -> "Direct",
      "Regular"/"Reg" -> "Regular"
    - Normalize growth option suffixes: "Growth"/"Gr"/"Growth Option" -> "Growth"
    - Apply title case

    Args:
        name: Raw scheme name string.

    Returns:
        Normalized scheme name.
    """
    if not name:
        return name

    s = name.strip()
    # Collapse multiple whitespace
    s = re.sub(r"\s+", " ", s)

    # Normalize plan type: Direct Plan / Direct / Dir -> Direct
    s = re.sub(r"\b(Direct\s+Plan|Dir)\b", "Direct", s, flags=re.IGNORECASE)

    # Normalize plan type: Regular Plan / Reg -> Regular
    s = re.sub(
        r"\b(Regular\s+Plan|Reg)\b", "Regular", s, flags=re.IGNORECASE
    )

    # Normalize growth suffixes: "Growth Option" / "Gr" -> "Growth"
    # Must handle "Gr" carefully to avoid replacing inside other words
    s = re.sub(r"\bGrowth\s+Option\b", "Growth", s, flags=re.IGNORECASE)
    s = re.sub(r"\bGr\b", "Growth", s, flags=re.IGNORECASE)

    # Remove duplicate "Growth" tokens that may result from double-substitution
    s = re.sub(r"(\bGrowth\b)(\s+\bGrowth\b)+", "Growth", s, flags=re.IGNORECASE)

    # Title case
    s = s.title()

    # Fix common title-case artifacts: preserve "Of", "And" only mid-name
    # but keep "Direct", "Regular", "Growth" capitalised correctly
    s = s.strip()
    return s


def normalize_sector_name(name: str, sector_mapping: dict) -> str:
    """Map AMC-specific sector names to canonical names.

    Uses a case-insensitive lookup in the provided mapping dict.
    E.g. "Financial Services" / "Financials" / "BFSI" -> "Financial Services"

    Args:
        name: Raw sector name from the factsheet.
        sector_mapping: Dict mapping variant names (lowercase) to canonical names.

    Returns:
        Canonical sector name, or title-cased original if no mapping found.
    """
    if not name:
        return name

    stripped = name.strip()
    lookup_key = stripped.lower()

    if lookup_key in sector_mapping:
        return sector_mapping[lookup_key]

    # Return cleaned-up original if no mapping exists
    return stripped.title()


def normalize_percentage(value) -> Optional[float]:
    """Normalize a percentage value to a float in percentage points.

    Handles:
        - "45.2%" -> 45.2
        - "0.452" (decimal fraction) -> 45.2
        - 45.2 (already percentage) -> 45.2

    Args:
        value: Raw percentage value (str, int, or float).

    Returns:
        Float percentage value, or None if unparseable.
    """
    if value is None:
        return None

    try:
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            if cleaned.endswith("%"):
                return float(cleaned.rstrip("%").strip())
            num = float(cleaned)
        else:
            num = float(value)

        # Heuristic: if absolute value is between -1 and 1 (exclusive),
        # treat as decimal fraction and convert to percentage.
        if -1 < num < 1 and num != 0:
            return round(num * 100, 4)
        return round(num, 4)
    except (ValueError, TypeError):
        logger.warning("Could not normalize percentage value: %r", value)
        return None


def normalize_aum(value, unit_hint: str = None) -> Optional[float]:
    """Normalize AUM to crores.

    Handles:
        - Lakhs -> crores conversion (divide by 100)
        - Absolute rupee values -> crores (divide by 1e7)
        - Already in crores -> pass through

    Args:
        value: Raw AUM value (str or numeric).
        unit_hint: Optional hint like "lakhs", "crores", "absolute".

    Returns:
        AUM in crores as a float, or None if unparseable.
    """
    if value is None:
        return None

    try:
        if isinstance(value, str):
            cleaned = value.strip().replace(",", "").replace("₹", "").strip()
            if not cleaned:
                return None
            num = float(cleaned)
        else:
            num = float(value)
    except (ValueError, TypeError):
        logger.warning("Could not normalize AUM value: %r", value)
        return None

    hint = (unit_hint or "").strip().lower()

    if hint == "lakhs" or hint == "lakh":
        return round(num / 100, 4)
    elif hint == "absolute":
        return round(num / 1e7, 4)
    elif hint == "crores" or hint == "crore" or hint == "cr":
        return round(num, 4)

    # Auto-detect based on magnitude when no hint is given
    if num > 1e6:
        # Likely absolute rupees
        return round(num / 1e7, 4)
    elif num > 1e4:
        # Likely lakhs
        return round(num / 100, 4)

    # Assume already in crores
    return round(num, 4)


def normalize_date(value) -> Optional[date]:
    """Parse various Indian date formats into a date object.

    Handles:
        - DD-MM-YYYY
        - DD/MM/YYYY
        - "March 31, 2025"
        - "31 March 2025"
        - "31-Mar-2025"
        - "31 Mar 2025"
        - YYYY-MM-DD (ISO)

    Args:
        value: Raw date value (str or date).

    Returns:
        A date object, or None if unparseable.
    """
    if value is None:
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, datetime):
        return value.date()

    if not isinstance(value, str):
        logger.warning("Unexpected date type: %r", value)
        return None

    s = value.strip()
    if not s:
        return None

    formats = [
        "%d-%m-%Y",       # 31-03-2025
        "%d/%m/%Y",       # 31/03/2025
        "%B %d, %Y",      # March 31, 2025
        "%d %B %Y",       # 31 March 2025
        "%d-%b-%Y",       # 31-Mar-2025
        "%d %b %Y",       # 31 Mar 2025
        "%Y-%m-%d",       # 2025-03-31 (ISO)
        "%d/%m/%y",       # 31/03/25
        "%d-%m-%y",       # 31-03-25
        "%b %d, %Y",      # Mar 31, 2025
    ]

    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    logger.warning("Could not parse date: %r", s)
    return None


def normalize_fund_manager_name(name: str) -> str:
    """Normalize fund manager names.

    - Strip common designations: CFA, MBA, CPA, FRM, PGDM, CA, B.Com, etc.
    - Apply title case.

    Args:
        name: Raw fund manager name.

    Returns:
        Cleaned, title-cased name.
    """
    if not name:
        return name

    s = name.strip()

    # Remove common professional designations (comma-separated or parenthesized)
    designations = (
        r"CFA|MBA|CPA|FRM|PGDM|PGDBM|CA|FCA|ACA|CMA|B\.?\s*Com|M\.?\s*Com|"
        r"B\.?\s*Tech|M\.?\s*Tech|B\.?\s*E|M\.?\s*E|Ph\.?\s*D|CAIA|CFP|"
        r"B\.?\s*Sc|M\.?\s*Sc|ACMA|ACS|FCS|CMA"
    )

    # Remove designations in parentheses, e.g. "John Doe (CFA, MBA)"
    s = re.sub(rf"\s*\((?:{designations})(?:\s*,\s*(?:{designations}))*\s*\)", "", s, flags=re.IGNORECASE)

    # Remove trailing comma-separated designations, e.g. "John Doe, CFA, MBA"
    s = re.sub(rf"(?:\s*,\s*(?:{designations}))+\s*$", "", s, flags=re.IGNORECASE)

    # Remove standalone trailing designations after a dash or pipe
    s = re.sub(rf"\s*[-|]\s*(?:{designations})(?:\s*,\s*(?:{designations}))*\s*$", "", s, flags=re.IGNORECASE)

    s = re.sub(r"\s+", " ", s).strip()
    s = s.title()
    return s


def deduplicate(
    schemes: list[SchemeFactsheet],
) -> list[SchemeFactsheet]:
    """Deduplicate scheme factsheets by (amc_name, scheme_name, factsheet_date).

    When duplicates exist, keep the one with the higher parse_confidence.

    Args:
        schemes: List of SchemeFactsheet, possibly containing duplicates.

    Returns:
        Deduplicated list.
    """
    best: dict[tuple, SchemeFactsheet] = {}

    for scheme in schemes:
        key = (scheme.amc_name, scheme.scheme_name, scheme.factsheet_date)
        existing = best.get(key)

        if existing is None:
            best[key] = scheme
        else:
            existing_conf = existing.parse_confidence or 0.0
            new_conf = scheme.parse_confidence or 0.0
            if new_conf > existing_conf:
                best[key] = scheme
                logger.debug(
                    "Dedup: replaced %s (confidence %.2f -> %.2f)",
                    key,
                    existing_conf,
                    new_conf,
                )

    removed = len(schemes) - len(best)
    if removed > 0:
        logger.info("Deduplication removed %d duplicate schemes", removed)

    return list(best.values())


def load_sector_mapping(path: str) -> dict:
    """Load sector name mapping from a YAML file.

    The YAML file should map canonical sector names to lists of variants, e.g.::

        Financial Services:
          - financials
          - bfsi
          - banking & financial services
        Information Technology:
          - it
          - technology
          - software

    The returned dict maps lowercase variant -> canonical name for O(1) lookup.

    Args:
        path: Path to the YAML file.

    Returns:
        Dict mapping lowercase variant names to canonical sector names.
    """
    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("Sector mapping file not found: %s", path)
        return {}
    except Exception:
        logger.exception("Failed to load sector mapping from: %s", path)
        return {}

    mapping: dict[str, str] = {}
    for canonical_name, variants in raw.items():
        # Map the canonical name itself (lowercase) to itself
        mapping[canonical_name.lower()] = canonical_name
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, str):
                    mapping[variant.strip().lower()] = canonical_name

    logger.info(
        "Loaded sector mapping with %d entries from %s", len(mapping), path
    )
    return mapping
