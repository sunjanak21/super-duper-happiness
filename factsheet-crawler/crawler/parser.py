"""Orchestrator that routes PDF parsing to the correct AMC-specific parser."""

import logging

from amcs import (
    BaseAMCParser,
    GenericAMCParser,
    HDFCAMCParser,
    ICICIAMCParser,
    SBIAMCParser,
)
from models.schema import SchemeFactsheet

logger = logging.getLogger(__name__)

_PARSER_REGISTRY: dict[str, type[BaseAMCParser]] = {
    "hdfc": HDFCAMCParser,
    "icici": ICICIAMCParser,
    "sbi": SBIAMCParser,
    "generic": GenericAMCParser,
}


def get_parser(parser_class_name: str) -> BaseAMCParser:
    """Instantiate and return the appropriate AMC parser.

    Args:
        parser_class_name: Key identifying the parser (e.g. "hdfc", "icici",
                           "sbi", "generic").

    Returns:
        An instance of the matching BaseAMCParser subclass.

    Raises:
        ValueError: If the parser_class_name is not recognised.
    """
    parser_class_name = parser_class_name.lower().strip()
    parser_cls = _PARSER_REGISTRY.get(parser_class_name)

    if parser_cls is None:
        logger.warning(
            "Unknown parser class '%s', falling back to GenericParser",
            parser_class_name,
        )
        parser_cls = GenericAMCParser

    logger.debug("Using parser: %s", parser_cls.__name__)
    return parser_cls()


def parse_factsheet(
    pdf_path: str, amc_config: dict
) -> list[SchemeFactsheet]:
    """Parse a factsheet PDF using the appropriate AMC-specific parser.

    Routes to the correct parser based on ``amc_config["parser_class"]``,
    instantiates it, and delegates parsing.  Exceptions raised during parsing
    are caught and logged so that failures for individual schemes do not halt
    the overall pipeline.

    Args:
        pdf_path: Filesystem path to the downloaded factsheet PDF.
        amc_config: AMC configuration dict; must contain a ``parser_class``
                     key (e.g. ``"hdfc"``, ``"icici"``, ``"sbi"``,
                     ``"generic"``).

    Returns:
        A list of :class:`SchemeFactsheet` objects extracted from the PDF.
        May be empty if parsing fails entirely.
    """
    parser_class_name = amc_config.get("parser_class", "generic")
    amc_name = amc_config.get("name", "Unknown AMC")

    logger.info(
        "Parsing factsheet for %s using '%s' parser: %s",
        amc_name,
        parser_class_name,
        pdf_path,
    )

    parser = get_parser(parser_class_name)
    results: list[SchemeFactsheet] = []

    # Extract factsheet date from the PDF path (directory name is YYYY-MM-DD)
    from datetime import date as date_type
    from pathlib import Path

    factsheet_date = date_type.today()
    try:
        dir_name = Path(pdf_path).stem  # e.g. "2026-03-29"
        factsheet_date = date_type.fromisoformat(dir_name)
    except (ValueError, AttributeError):
        logger.debug("Could not parse date from PDF path, using today's date")

    try:
        results = parser.parse_factsheet(pdf_path, amc_name, factsheet_date)
        logger.info(
            "Successfully parsed %d scheme(s) from %s",
            len(results),
            pdf_path,
        )
    except Exception:
        logger.exception(
            "Failed to parse factsheet for %s from %s",
            amc_name,
            pdf_path,
        )

    return results
