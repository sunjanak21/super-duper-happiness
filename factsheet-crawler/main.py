"""Entry point for the mutual fund factsheet pipeline.

Supports scheduled execution via APScheduler and immediate one-off runs
with the ``--run-now`` CLI flag.
"""

import argparse
import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from crawler.fetcher import fetch_all_factsheets
from crawler.parser import parse_factsheet
from crawler.normalizer import normalize_all
from crawler.compute import compute_derived_metrics
from crawler.exporter import export_to_excel
from models.schema import DownloadStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Load pipeline configuration from a YAML file.

    Args:
        path: Filesystem path to the YAML configuration file.

    Returns:
        Parsed configuration as a dictionary.
    """
    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    logger.info("Loaded configuration from %s", path)
    return config


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    config: dict,
    force_download: bool = False,
    target_amcs: list[str] | None = None,
) -> None:
    """Execute the full factsheet pipeline.

    Steps:
        1. Fetch all factsheet PDFs (async downloads).
        2. Parse each successfully downloaded PDF.
        3. Normalize parsed data.
        4. Compute derived / enriched metrics.
        5. Export results to Excel.
        6. Log a summary of outcomes.

    Args:
        config: Full pipeline configuration dictionary.
        force_download: Re-download PDFs even if cached locally.
        target_amcs: If provided, only crawl these AMC slugs.
    """
    logger.info("Pipeline started")

    # Apply runtime overrides to config
    if force_download:
        config.setdefault("fetcher", {})["force_download"] = True
    if target_amcs:
        config["amcs"] = [
            a for a in config.get("amcs", []) if a["slug"] in target_amcs
        ]
        logger.info("Filtered to target AMCs: %s", target_amcs)

    # 1. Fetch factsheets
    amc_configs = config.get("amcs", [])
    download_results = await fetch_all_factsheets(config)

    successes = [r for r in download_results if r.status == DownloadStatus.SUCCESS]
    failures = [r for r in download_results if r.status == DownloadStatus.FAILED]
    logger.info(
        "Downloads complete: %d succeeded, %d failed, %d total",
        len(successes),
        len(failures),
        len(download_results),
    )

    # 2. Parse each successful download
    all_schemes = []
    parse_failure_count = 0
    for result in successes:
        amc_cfg = next(
            (a for a in amc_configs if a["slug"] == result.amc_slug), {}
        )
        try:
            schemes = parse_factsheet(result.path, amc_cfg)
            all_schemes.extend(schemes)
        except Exception:
            logger.exception(
                "Unexpected error parsing %s for %s",
                result.path,
                result.amc_name,
            )
            parse_failure_count += 1

    # 3. Normalize
    normalized = normalize_all(all_schemes, config)

    # 4. Compute derived metrics
    enriched = compute_derived_metrics(normalized, config)

    # 5. Export to Excel
    total_warnings = sum(len(s.parse_warnings) for s in enriched)
    output_path = export_to_excel(enriched, config)

    # 6. Summary — printed in exact format parsed by the GitHub Actions workflow
    total_failures = len(failures) + parse_failure_count
    summary = (
        f"Pipeline complete. Schemes parsed: {len(enriched)} | "
        f"Warnings: {total_warnings} | Failures: {total_failures}"
    )
    print(summary)
    logger.info("%s. Output: %s", summary, output_path)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    """Configure root logger with rotating file handler and console handler."""
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Rotating file handler (10 MB, keep 5 backups)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "pipeline.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def _build_cron_trigger(config: dict) -> CronTrigger:
    """Build an APScheduler CronTrigger from schedule config."""
    schedule = config.get("schedule", {})
    mode = schedule.get("mode", "weekly")
    hour = schedule.get("hour", 6)
    minute = schedule.get("minute", 0)

    if mode == "daily":
        return CronTrigger(hour=hour, minute=minute)

    day_of_week = schedule.get("day_of_week", "mon")
    return CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute)


def _scheduled_run(config: dict) -> None:
    """Wrapper that calls the async pipeline from the synchronous scheduler."""
    asyncio.run(run_pipeline(config))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Mutual fund factsheet pipeline"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run the pipeline immediately instead of waiting for the schedule",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download PDFs even if cached locally",
    )
    parser.add_argument(
        "--amcs",
        type=str,
        default="",
        help="Comma-separated AMC slugs to crawl (empty = all)",
    )
    args = parser.parse_args()

    _setup_logging()
    config = load_config(args.config)

    target_amcs = [s.strip() for s in args.amcs.split(",") if s.strip()] or None

    if args.run_now:
        logger.info("Immediate run requested via --run-now")
        asyncio.run(run_pipeline(
            config,
            force_download=args.force_download,
            target_amcs=target_amcs,
        ))
        return

    # Scheduled execution
    trigger = _build_cron_trigger(config)
    scheduler = BlockingScheduler()
    scheduler.add_job(_scheduled_run, trigger, args=[config])

    logger.info("Scheduler started. Waiting for next trigger...")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shut down")


if __name__ == "__main__":
    main()
