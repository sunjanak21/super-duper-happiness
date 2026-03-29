"""Excel exporter for enriched mutual fund factsheet data."""

import logging
from datetime import date
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Font, numbers
from openpyxl.utils import get_column_letter

from models.schema import EnrichedSchemeFactsheet

logger = logging.getLogger(__name__)


def _auto_fit_columns(ws) -> None:
    """Auto-fit column widths based on cell content."""
    for col_idx, column_cells in enumerate(ws.columns, start=1):
        max_length = 0
        for cell in column_cells:
            if cell.value is not None:
                max_length = max(max_length, len(str(cell.value)))
        adjusted_width = min(max_length + 2, 60)
        ws.column_dimensions[get_column_letter(col_idx)].width = adjusted_width


def _apply_header_formatting(ws) -> None:
    """Apply bold headers, freeze top row, and auto-filter."""
    bold_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold_font
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _format_number_cells(ws, pct_columns: set[int], num_columns: set[int]) -> None:
    """Apply number formats to percentage and numeric columns."""
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            col_idx = cell.column
            if col_idx in pct_columns and cell.value is not None:
                cell.number_format = "0.00%"
                # Convert from percentage value (e.g. 8.5) to decimal (0.085)
                if isinstance(cell.value, (int, float)):
                    cell.value = cell.value / 100.0
            elif col_idx in num_columns and cell.value is not None:
                cell.number_format = "#,##0.00"


def _get_primary_fm_name(scheme: EnrichedSchemeFactsheet) -> Optional[str]:
    """Get the primary (first) fund manager name."""
    if scheme.fund_managers:
        return scheme.fund_managers[0].name
    return None


def _get_primary_fm_tenure(scheme: EnrichedSchemeFactsheet) -> Optional[float]:
    """Get the primary fund manager's tenure in years."""
    return scheme.fm_tenure_years


def _build_master_sheet(wb: Workbook, schemes: list[EnrichedSchemeFactsheet]) -> None:
    """Build the Master sheet with one row per scheme."""
    ws = wb.active
    ws.title = "Master"

    headers = [
        "AMC Name", "Scheme Name", "Scheme Category", "AMFI Code",
        "Factsheet Date", "Primary FM Name", "Primary FM Tenure (Yrs)",
        "AUM (Cr)", "NAV", "NAV Date", "Expense Ratio (%)",
        "Portfolio Turnover (%)", "Portfolio P/E", "Portfolio P/B",
        "Benchmark", "Inception Date", "Total Holdings",
        "Top 5 Weight (%)", "Top 10 Weight (%)", "HHI",
        "Tail Weight (%)", "Equity Holdings", "All Holdings",
        "Equity Allocation (%)", "Debt Allocation (%)", "Cash Allocation (%)",
        "Sector Count", "Top 3 Sector Weight (%)", "Earnings Yield",
        "Parse Confidence", "Source PDF",
    ]
    ws.append(headers)

    # Track percentage and number columns (1-indexed)
    pct_cols = {11, 12, 18, 19, 21, 24, 25, 26, 28}
    num_cols = {7, 8, 9, 13, 14, 17, 20, 22, 23, 27, 29, 30}

    for s in schemes:
        ws.append([
            s.amc_name, s.scheme_name, s.scheme_category, s.amfi_code,
            s.factsheet_date.isoformat() if s.factsheet_date else None,
            _get_primary_fm_name(s), _get_primary_fm_tenure(s),
            s.aum_cr, s.nav,
            s.nav_date.isoformat() if s.nav_date else None,
            s.expense_ratio_pct, s.portfolio_turnover_pct,
            s.portfolio_pe, s.portfolio_pb,
            s.benchmark,
            s.inception_date.isoformat() if s.inception_date else None,
            s.total_holdings_count,
            s.top5_weight_pct, s.top10_weight_pct, s.hhi,
            s.tail_weight_pct, s.equity_holdings_count, s.all_holdings_count,
            s.equity_allocation_pct, s.debt_allocation_pct, s.cash_allocation_pct,
            s.sector_count, s.top3_sector_weight_pct, s.earnings_yield,
            s.parse_confidence, s.source_pdf_path,
        ])

    _apply_header_formatting(ws)
    _format_number_cells(ws, pct_cols, num_cols)
    _auto_fit_columns(ws)


def _build_holdings_detail_sheet(
    wb: Workbook, schemes: list[EnrichedSchemeFactsheet]
) -> None:
    """Build the Holdings Detail sheet with one row per holding per scheme."""
    ws = wb.create_sheet("Holdings Detail")

    headers = [
        "Scheme Name", "AMC Name", "Stock Name", "Sector",
        "Weight (%)", "Instrument Type",
    ]
    ws.append(headers)

    pct_cols = {5}
    num_cols: set[int] = set()

    for s in schemes:
        for h in s.top_holdings:
            ws.append([
                s.scheme_name, s.amc_name, h.stock_name,
                h.sector, h.weight_pct, h.instrument_type,
            ])

    _apply_header_formatting(ws)
    _format_number_cells(ws, pct_cols, num_cols)
    _auto_fit_columns(ws)


def _build_sector_allocation_sheet(
    wb: Workbook, schemes: list[EnrichedSchemeFactsheet]
) -> None:
    """Build the Sector Allocation sheet with one row per sector per scheme."""
    ws = wb.create_sheet("Sector Allocation")

    headers = ["Scheme Name", "AMC Name", "Sector Name", "Weight (%)"]
    ws.append(headers)

    pct_cols = {4}
    num_cols: set[int] = set()

    for s in schemes:
        for sa in s.sector_allocation:
            ws.append([
                s.scheme_name, s.amc_name, sa.sector_name, sa.weight_pct,
            ])

    _apply_header_formatting(ws)
    _format_number_cells(ws, pct_cols, num_cols)
    _auto_fit_columns(ws)


def _build_fund_managers_sheet(
    wb: Workbook, schemes: list[EnrichedSchemeFactsheet]
) -> None:
    """Build the Fund Managers sheet with one row per FM per scheme."""
    ws = wb.create_sheet("Fund Managers")

    headers = [
        "Scheme Name", "AMC Name", "FM Name", "Managing Since", "Tenure (Yrs)",
    ]
    ws.append(headers)

    num_cols = {5}

    for s in schemes:
        for fm in s.fund_managers:
            tenure = fm.experience_years
            ws.append([
                s.scheme_name, s.amc_name, fm.name,
                fm.managing_since.isoformat() if fm.managing_since else None,
                tenure,
            ])

    _apply_header_formatting(ws)
    _format_number_cells(ws, set(), num_cols)
    _auto_fit_columns(ws)


def _build_parse_log_sheet(
    wb: Workbook, schemes: list[EnrichedSchemeFactsheet]
) -> None:
    """Build the Parse Log sheet with parse metadata per scheme."""
    ws = wb.create_sheet("Parse Log")

    headers = [
        "Scheme Name", "AMC Name", "Parse Confidence", "Warnings",
        "Source PDF Path",
    ]
    ws.append(headers)

    num_cols = {3}

    for s in schemes:
        warnings_str = "; ".join(s.parse_warnings) if s.parse_warnings else ""
        ws.append([
            s.scheme_name, s.amc_name, s.parse_confidence,
            warnings_str, s.source_pdf_path,
        ])

    _apply_header_formatting(ws)
    _format_number_cells(ws, set(), num_cols)
    _auto_fit_columns(ws)


def export_to_excel(
    schemes: list[EnrichedSchemeFactsheet], config: dict
) -> str:
    """Export enriched scheme factsheets to a multi-sheet Excel workbook.

    Args:
        schemes: List of enriched scheme factsheets to export.
        config: Configuration dict; expects config["output"]["excel_path"]
                with an optional {date} placeholder.

    Returns:
        The output file path of the created Excel workbook.
    """
    output_template = config.get("output", {}).get(
        "excel_path", "data/output/factsheet_master_{date}.xlsx"
    )
    output_path = output_template.replace("{date}", date.today().isoformat())

    logger.info("Exporting %d schemes to Excel: %s", len(schemes), output_path)

    wb = Workbook()

    _build_master_sheet(wb, schemes)
    _build_holdings_detail_sheet(wb, schemes)
    _build_sector_allocation_sheet(wb, schemes)
    _build_fund_managers_sheet(wb, schemes)
    _build_parse_log_sheet(wb, schemes)

    wb.save(output_path)
    logger.info("Excel export complete: %s", output_path)

    return output_path
