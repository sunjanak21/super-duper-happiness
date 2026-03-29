"""AMC-specific factsheet parsers.

Each parser inherits from BaseAMCParser and overrides only the methods
where the AMC's PDF layout differs from the generic patterns.
"""

from .base import BaseAMCParser
from .generic import GenericAMCParser
from .hdfc import HDFCAMCParser
from .icici import ICICIAMCParser
from .sbi import SBIAMCParser

# Mapping from AMC slug to parser class for easy dispatch
AMC_PARSERS: dict[str, type[BaseAMCParser]] = {
    "hdfc": HDFCAMCParser,
    "icici": ICICIAMCParser,
    "sbi": SBIAMCParser,
}


def get_parser(amc_slug: str) -> BaseAMCParser:
    """Return the appropriate parser instance for the given AMC.

    Falls back to GenericAMCParser when no AMC-specific parser exists.
    """
    cls = AMC_PARSERS.get(amc_slug.lower(), GenericAMCParser)
    return cls()


__all__ = [
    "BaseAMCParser",
    "GenericAMCParser",
    "HDFCAMCParser",
    "ICICIAMCParser",
    "SBIAMCParser",
    "AMC_PARSERS",
    "get_parser",
]
