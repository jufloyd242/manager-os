"""Manager OS — CLI entry point."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="manager-os",
    help="Local-first consulting manager dashboard.",
    add_completion=False,
)

console = Console()


# ---------------------------------------------------------------------------
# Skip-reason helpers (used by ingest and extract verbose output)
# ---------------------------------------------------------------------------

_SAFE_SKIP_REASONS: frozenset[str] = frozenset({
    "already_exists",
    "duplicate_content_hash",
    "signal_already_exists",
    "action_item_already_exists",
    "decision_already_exists",
    # Source tier skips — notes that are not signal tier are intentionally skipped
    "tier_context",
    "tier_excluded",
    "junk_note_type",
    # Calendar events with no external attendees (solo timeblocks) — intentional
    "no_external_attendees",
})


def _all_skips_safe(source_results: list[tuple[str, object]]) -> bool:
    for _, r in source_results:
        for reason in getattr(r, "skip_reasons", {}).keys():
            if reason not in _SAFE_SKIP_REASONS:
                return False
    return True


def _print_skip_info(
    source_results: list[tuple[str, object]],
    verbose: bool,
) -> None:
    """Print skip counts with explanations after the main results table."""
    total_skipped = sum(getattr(r, "skipped", 0) for _, r in source_results)
    if total_skipped == 0:
        return

    if verbose:
        skip_table = Table(
            title="Skip reasons",
            show_header=True,
            show_edge=False,
            box=None,
            pad_edge=False,
        )
        skip_table.add_column("Source / Step", style="cyan", no_wrap=True)
        skip_table.add_column("Reason", no_wrap=True)
        skip_table.add_column("Count", justify="right")
        skip_table.add_column("", justify="left")  # safe indicator

        for label, result in source_results:
            reasons = getattr(result, "skip_reasons", {})
            for reason, count in sorted(reasons.items()):
                safe = reason in _SAFE_SKIP_REASONS
                indicator = (
                    "[dim]✓ already exists — safe to re-run[/dim]"
                    if safe
                    else "[yellow]⚠ check data[/yellow]"
                )
                skip_table.add_row(label, reason.replace("_", " "), str(count), indicator)

        if skip_table.row_count:
            console.print()
            console.print(skip_table)
    else:
        safe = _all_skips_safe(source_results)
        if safe:
            console.print(
                f"[dim]  {total_skipped} record(s) skipped — already exist "
                f"(idempotent re-run). Pass --verbose for details.[/dim]"
            )
        else:
            console.print(
                f"[yellow]  {total_skipped} record(s) skipped — some may need "
                f"attention. Pass --verbose for details.[/yellow]"
            )


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# Dry-run helpers
# ---------------------------------------------------------------------------

# Directories skipped when scanning an Obsidian vault for dry-run preview.
_DRY_RUN_OBSIDIAN_SKIP_DIRS: frozenset[str] = frozenset({".obsidian", ".git", ".trash"})


def _dry_run_open_ro(db_path: str):
    """Open the existing database in read-only mode for duplicate checks.

    Returns an open connection or None if the DB does not exist / cannot
    be opened (e.g. first run before any ingest).
    """
    import duckdb as _duckdb

    if db_path == ":memory:":
        return None
    p = Path(db_path)
    if not p.exists():
        return None
    try:
        return _duckdb.connect(str(p), read_only=True)
    except Exception:
        return None


def _scan_obsidian_dry(vault_path: str, ro_conn) -> tuple[int, int, list[str]]:
    """Scan an Obsidian vault without writing. Returns (new, dup, warnings)."""
    from manager_os.db import content_hash as _ch

    vault = Path(vault_path)
    if not vault.exists():
        return 0, 0, [f"Vault not found: {vault_path}"]

    new_count = dup_count = 0
    for md_file in vault.rglob("*.md"):
        if any(part.startswith(".") for part in md_file.parts):
            continue
        if any(skip in md_file.parts for skip in _DRY_RUN_OBSIDIAN_SKIP_DIRS):
            continue
        if ro_conn is not None:
            try:
                raw_text = md_file.read_text(encoding="utf-8", errors="replace")
                c_hash = _ch(raw_text)
                source_path = str(md_file.resolve())
                row = ro_conn.execute(
                    "SELECT id FROM raw_documents "
                    "WHERE source_path = ? AND content_hash = ?",
                    [source_path, c_hash],
                ).fetchone()
                if row:
                    dup_count += 1
                else:
                    new_count += 1
            except Exception:
                new_count += 1
        else:
            new_count += 1
    return new_count, dup_count, []


def _scan_csv_dry(
    csv_path: Optional[str],
    ro_conn,
    db_table: str,
) -> tuple[int, int, list[str]]:
    """Read a CSV and report row count. Returns (rows, 0, warnings).

    Exact duplicate checking is omitted because it requires fully parsing each
    row and computing its stable ID — that is equivalent to running the real
    ingestor. Instead, if a DB connection is available we show how many rows
    are already in the target table as a reference.
    """
    import pandas as _pd

    if not csv_path:
        return 0, 0, ["path not configured"]
    try:
        df = _pd.read_csv(csv_path, dtype=str)
    except Exception as exc:
        return 0, 0, [f"could not read: {exc}"]

    n = len(df)
    notes: list[str] = []
    if ro_conn is not None:
        try:
            existing = ro_conn.execute(
                f"SELECT COUNT(*) FROM {db_table}"
            ).fetchone()[0]
            if existing:
                notes.append(f"{existing} already in DB")
        except Exception:
            pass
    return n, 0, notes


def _scan_summary_dry(
    summary_dir: Optional[str],
    target_date: "date",
    ro_conn,
) -> tuple[int, int, list[str]]:
    """Check whether a summary file exists for target_date. Returns (new, dup, warnings)."""
    from manager_os.db import content_hash as _ch

    if not summary_dir:
        return 0, 0, ["path not configured"]
    for ext in (".md", ".txt"):
        candidate = Path(summary_dir) / f"{target_date.isoformat()}{ext}"
        if candidate.exists():
            if ro_conn is not None:
                try:
                    doc_id = _ch(str(candidate.resolve()))
                    row = ro_conn.execute(
                        "SELECT id FROM raw_documents WHERE id = ?", [doc_id]
                    ).fetchone()
                    if row:
                        return 0, 1, []
                except Exception:
                    pass
            return 1, 0, []
    return 0, 0, [f"no summary file for {target_date}"]


def _scan_gws_dry(gws_dir: Optional[str]) -> tuple[int, int, list[str]]:
    """Count GWS snapshot JSON files without reading them."""
    if not gws_dir:
        return 0, 0, ["path not configured"]
    d = Path(gws_dir)
    if not d.exists():
        return 0, 0, [f"directory not found: {gws_dir}"]
    count = sum(1 for _ in d.rglob("*.json"))
    return count, 0, []


def _do_dry_run_ingest(
    source: str,
    target_date: "date",
    settings,
) -> None:
    """Print a dry-run preview table for ingest — no DB writes."""
    from rich import box as rich_box
    from rich.panel import Panel

    ro_conn = _dry_run_open_ro(settings.db_path)
    db_status = (
        "read-only duplicate check against existing DB"
        if ro_conn is not None
        else "no existing DB — all items assumed new"
    )

    console.print()
    console.print(
        Panel.fit(
            "[bold]Manager OS — Dry Run: Ingest Preview[/bold]",
            box=rich_box.ROUNDED,
            border_style="yellow",
        )
    )
    console.print(f"  [dim]Date:[/dim]    {target_date}")
    console.print(f"  [dim]Source:[/dim]  {source}")
    console.print(f"  [dim]DB:[/dim]      {settings.db_path}  [{db_status}]")
    console.print()

    tbl = Table(
        title=f"Would ingest — {target_date}  (dry run, nothing written)",
        show_header=True,
        header_style="bold",
        box=rich_box.SIMPLE,
        show_edge=False,
        pad_edge=False,
    )
    tbl.add_column("Source", style="cyan")
    tbl.add_column("Would Write", justify="right", style="green")
    tbl.add_column("Would Skip", justify="right", style="yellow")
    tbl.add_column("Notes", style="dim")

    if source in ("all", "obsidian"):
        if settings.vault_path:
            new, dup, warns = _scan_obsidian_dry(settings.vault_path, ro_conn)
            tbl.add_row("obsidian", str(new), str(dup), "; ".join(warns))
        else:
            tbl.add_row("obsidian", "—", "—", "MANAGER_OS_VAULT_PATH not set")

    if source in ("all", "forecast"):
        new, dup, warns = _scan_csv_dry(
            settings.forecast_csv, ro_conn, "staffing_forecast"
        )
        tbl.add_row("forecast", str(new), str(dup), "; ".join(warns))

    if source in ("all", "deals"):
        new, dup, warns = _scan_csv_dry(settings.deals_csv, ro_conn, "deals")
        tbl.add_row("deals", str(new), str(dup), "; ".join(warns))

    if source in ("all", "summary"):
        new, dup, warns = _scan_summary_dry(
            settings.workspace_summary_dir, target_date, ro_conn
        )
        tbl.add_row("summary", str(new), str(dup), "; ".join(warns))

    if source in ("all", "gws"):
        new, dup, warns = _scan_gws_dry(settings.gws_snapshot_dir)
        tbl.add_row("gws", str(new), str(dup), "; ".join(warns))

    if source in ("all", "workspace"):
        _add_workspace_dry_run_rows(tbl, target_date)

    if ro_conn is not None:
        ro_conn.close()

    console.print(tbl)
    console.print()
    console.print(
        "[yellow bold]⚠  Dry run — nothing was written to the database.[/yellow bold]"
    )
    console.print()


def _add_workspace_dry_run_rows(tbl: Table, target_date: "date") -> None:
    """Add dry-run rows for workspace snapshot sources."""
    from manager_os.ingest.workspace_snapshot import _snapshot_exists

    subs = [
        ("ws-forecast", "forecast"),
        ("ws-calendar", "calendar"),
        ("ws-activity", "activity"),
    ]
    for label, subdir in subs:
        exists = _snapshot_exists(subdir, target_date)
        if exists:
            tbl.add_row(label, "1", "0", "snapshot found")
        else:
            tbl.add_row(label, "—", "—", f"no snapshot for {target_date}")


def _do_workspace_ingest(
    conn,
    settings,
    target_date: "date",
    force: bool = False,
    fetch: bool = False,
) -> "object":
    """Run workspace snapshot ingestion.

    If *fetch* is True, first retrieves fresh data from Google Workspace
    via Gemini CLI YOLO mode, then ingests the resulting snapshots.
    Without *fetch*, only reads pre-existing snapshot files.
    """
    import os as _os

    from manager_os.ingest.workspace_snapshot import (
        IngestResult,
        ingest_workspace_forecast_snapshot,
        ingest_workspace_calendar_snapshot,
        ingest_workspace_activity_snapshot,
    )

    # Safety check — read from Settings (which loads .env) rather than raw os.environ
    enabled = getattr(settings, "workspace_retrieval_enabled", False) or \
              _os.environ.get("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false").lower() in ("true", "yes", "1")
    if not enabled:
        result = IngestResult()
        result.errors.append(
            "Workspace retrieval is disabled. Set MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED=true in .env."
        )
        return result

    if fetch:
        console.print("[dim]  → Fetching workspace data via Gemini CLI…[/dim]")
        try:
            _run_workspace_fetch(target_date)
        except Exception as exc:
            result = IngestResult()
            result.errors.append(f"Workspace fetch failed: {exc}")
            return result

    result = IngestResult()

    for label, fn in [
        ("forecast", ingest_workspace_forecast_snapshot),
        ("calendar", ingest_workspace_calendar_snapshot),
        ("activity", ingest_workspace_activity_snapshot),
    ]:
        r = fn(conn, target_date, force=force)
        result.ingested += r.ingested
        result.skipped += r.skipped
        result.failed += r.failed
        result.errors.extend(r.errors)

    return result


def _run_workspace_fetch(target_date: "date") -> None:
    """Run workspace fetch-all via Gemini CLI. Raises on failure."""
    from manager_os.ingest.workspace_gemini import (
        retrieve_forecast,
        retrieve_calendar,
        retrieve_activity,
    )

    for label, fn in [
        ("forecast", retrieve_forecast),
        ("calendar", retrieve_calendar),
        ("activity", retrieve_activity),
    ]:
        console.print(f"[dim]    Fetching {label}…[/dim]")
        r = fn(target_date, use_yolo=True)
        if not r.ok:
            console.print(f"[yellow]    ⚠ {label}: {r.error}[/yellow]")
        else:
            console.print(f"[dim]    ✓ {label}: {len(r.items)} item(s) → {r.written_to}[/dim]")


def _do_dry_run_extract(
    settings,
    run_date: "date",
    mode: str,
    llm_limit: int | None = None,
    llm_source_path: Optional[str] = None,
    llm_note_id: Optional[str] = None,
    llm_since_days: Optional[int] = None,
) -> None:
    """Print a dry-run preview for extract — copies notes to in-memory DB,
    runs extraction there, reports counts. The real DB is never written."""
    import duckdb as _duckdb

    from manager_os.db import init_schema
    from manager_os.extract.signals import run_rule_extraction
    from manager_os.extract.action_items import extract_action_items_from_all_notes
    from manager_os.extract.decisions import extract_decisions_from_all_notes
    from rich import box as rich_box
    from rich.panel import Panel

    ro_conn = _dry_run_open_ro(settings.db_path)
    if ro_conn is None:
        console.print(
            "[yellow]No existing database found. "
            "Run 'manager-os ingest' first.[/yellow]"
        )
        return

    try:
        note_count = ro_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    except Exception:
        note_count = 0

    if note_count == 0:
        console.print(
            "[red]No notes found. Run 'manager-os ingest' first.[/red]"
        )
        ro_conn.close()
        raise typer.Exit(1)

    # Build an in-memory DB and copy the tables the extraction rules need.
    mem_conn = _duckdb.connect(":memory:")
    init_schema(mem_conn)
    for tbl_name in ("notes", "staffing_forecast", "deals", "people", "clients"):
        try:
            ro_conn.execute(f"SELECT * FROM {tbl_name}")
            col_names = [desc[0] for desc in ro_conn.description]
            rows = ro_conn.fetchall()
            if not rows:
                continue
            quoted = ", ".join(f'"{c}"' for c in col_names)
            placeholders = ", ".join("?" for _ in col_names)
            for row in rows:
                mem_conn.execute(
                    f"INSERT OR IGNORE INTO {tbl_name} ({quoted}) "
                    f"VALUES ({placeholders})",
                    list(row),
                )
        except Exception:
            pass

    ro_conn.close()

    console.print()
    console.print(
        Panel.fit(
            "[bold]Manager OS — Dry Run: Extract Preview[/bold]",
            box=rich_box.ROUNDED,
            border_style="yellow",
        )
    )
    console.print(f"  [dim]Date:[/dim]   {run_date}")
    console.print(f"  [dim]Notes:[/dim]  {note_count} note(s) in database")
    if mode in ("llm", "both"):
        limit_str = str(llm_limit) if llm_limit is not None else "unlimited"
        console.print(f"  [dim]LLM limit:[/dim] {limit_str}")
        if llm_source_path:
            console.print(f"  [dim]LLM source path:[/dim] {llm_source_path}")
        if llm_note_id:
            console.print(f"  [dim]LLM note id:[/dim] {llm_note_id}")
        if llm_since_days:
            console.print(f"  [dim]LLM since days:[/dim] {llm_since_days}")
    console.print()

    tbl = Table(
        title=f"Would extract — {run_date}  (dry run, nothing written)",
        show_header=True,
        header_style="bold",
        box=rich_box.SIMPLE,
        show_edge=False,
        pad_edge=False,
    )
    tbl.add_column("Step", style="cyan")
    tbl.add_column("Would Write", justify="right", style="green")
    tbl.add_column("Would Skip", justify="right", style="yellow")
    tbl.add_column("Failed", justify="right", style="red")

    _step_results: list[tuple[str, object]] = []

    if mode in ("rules", "both"):
        result = run_rule_extraction(mem_conn, run_date=run_date)
        tbl.add_row(
            "signals (rules)",
            str(result.written),
            str(result.skipped),
            str(result.failed),
        )
        _step_results.append(("signals (rules)", result))

    if mode in ("llm", "both"):
        from manager_os.extract.llm_signals import run_llm_extraction, LLMExtractionUnavailable
        try:
            llm_result = run_llm_extraction(
                mem_conn,
                run_date=run_date,
                max_candidates=llm_limit,
                source_path_filter=llm_source_path,
                note_id=llm_note_id,
                since_days=llm_since_days,
            )
            tbl.add_row(
                "signals (llm)",
                str(llm_result.written),
                str(llm_result.skipped),
                str(llm_result.failed),
            )
            _step_results.append(("signals (llm)", llm_result))
        except LLMExtractionUnavailable as exc:
            console.print(f"[yellow]LLM extraction skipped: {exc}[/yellow]")

    ai_result = extract_action_items_from_all_notes(mem_conn)
    tbl.add_row(
        "action items",
        str(ai_result.written),
        str(ai_result.skipped),
        str(ai_result.failed),
    )
    _step_results.append(("action items", ai_result))

    dec_result = extract_decisions_from_all_notes(mem_conn)
    tbl.add_row(
        "decisions",
        str(dec_result.written),
        str(dec_result.skipped),
        str(dec_result.failed),
    )
    _step_results.append(("decisions", dec_result))

    mem_conn.close()

    console.print(tbl)
    console.print()
    console.print(
        "[yellow bold]⚠  Dry run — nothing was written to the database.[/yellow bold]"
    )
    console.print()


# ---------------------------------------------------------------------------
# manager-os ingest
# ---------------------------------------------------------------------------


@app.command()
def ingest(
    source: str = typer.Option(
        "all",
        "--source",
        help="Source to ingest: all | obsidian | forecast | deals | summary | gws | workspace",
    ),
    ingest_date: Optional[str] = typer.Option(
        None,
        "--date",
        help="Date to ingest (YYYY-MM-DD). Defaults to today.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-ingest files even if content hash is unchanged.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show skip reason details after the results table.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview what would be ingested without writing to the database.",
    ),
    workspace_fetch: bool = typer.Option(
        False,
        "--fetch",
        help="When ingesting workspace, first retrieve fresh data from Google Workspace via Gemini CLI.",
    ),
) -> None:
    """Ingest data from configured sources into DuckDB."""
    from manager_os.config import (
        load_source_priority,
        get_settings,
    )
    from manager_os.db import get_connection
    from manager_os.ingest.obsidian import ingest_vault
    from manager_os.ingest.forecast import ingest_forecast
    from manager_os.ingest.deals import ingest_deals
    from manager_os.ingest.workspace_summary import ingest_summary

    settings = get_settings()
    target_date = date.fromisoformat(ingest_date) if ingest_date else date.today()

    valid_sources = {"all", "obsidian", "forecast", "deals", "summary", "gws", "workspace"}
    if source not in valid_sources:
        console.print(f"[red]Unknown source '{source}'. Must be one of: {', '.join(sorted(valid_sources))}[/red]")
        raise typer.Exit(1)

    if workspace_fetch and source not in ("all", "workspace"):
        console.print("[yellow]--fetch only applies to --source workspace or all. Ignored.[/yellow]")

    if dry_run:
        _do_dry_run_ingest(source, target_date, settings)
        return

    try:
        sp = load_source_priority(settings)
    except Exception:
        sp = None

    conn = get_connection(settings.db_path)

    # Seed people/clients from config (idempotent, does not overwrite enriched data)
    try:
        from manager_os.db import seed_from_config
        seeded = seed_from_config(conn, settings)
        if seeded["people"] or seeded["clients"]:
            console.print(
                f"[dim]Seeded from config: {seeded['people']} people, {seeded['clients']} clients[/dim]"
            )
    except Exception:
        pass

    table = Table(title=f"Ingest results — {target_date}", show_header=True)
    table.add_column("Source", style="cyan")
    table.add_column("Ingested", justify="right", style="green")
    table.add_column("Warn", justify="right", style="yellow")
    table.add_column("Skipped", justify="right", style="dim")
    table.add_column("Failed", justify="right", style="red")

    run_obsidian = source in ("all", "obsidian")
    run_forecast = source in ("all", "forecast")
    run_deals = source in ("all", "deals")
    run_summary = source in ("all", "summary")
    run_gws = source in ("all", "gws")
    run_workspace = source in ("all", "workspace")

    had_error = False
    _source_results: list[tuple[str, object]] = []

    if run_obsidian:
        if not settings.vault_path:
            console.print("[red]MANAGER_OS_VAULT_PATH is not set. Set it in your .env file.[/red]")
            had_error = True
        else:
            try:
                r = ingest_vault(settings.vault_path, conn, force=force)
                table.add_row(
                    "obsidian",
                    str(r.ingested),
                    str(r.ingested_with_warnings),
                    str(r.skipped),
                    str(r.failed),
                )
                _source_results.append(("obsidian", r))
                if r.ingested_with_warnings:
                    for w in r.warnings[:10]:  # cap output at 10 warnings
                        console.print(f"  [yellow]⚠ frontmatter:[/yellow] {w}")
                if r.failed:
                    had_error = True
            except FileNotFoundError as exc:
                console.print(f"[red]Vault not found: {exc}[/red]")
                had_error = True

    if run_forecast:
        try:
            r = ingest_forecast(settings.forecast_csv, conn, source_priority=sp, force=force, settings=settings)
            table.add_row("forecast", str(r.ingested), "0", str(r.skipped), str(r.failed))
            _source_results.append(("forecast", r))
            if r.failed:
                had_error = True
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[red]Forecast CSV error: {exc}[/red]")
            had_error = True

    if run_deals:
        try:
            r = ingest_deals(settings.deals_csv, conn, source_priority=sp, force=force)
            table.add_row("deals", str(r.ingested), "0", str(r.skipped), str(r.failed))
            _source_results.append(("deals", r))
            if r.failed:
                had_error = True
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[red]Deals CSV error: {exc}[/red]")
            had_error = True

    if run_summary:
        r = ingest_summary(settings.workspace_summary_dir, target_date, conn, force=force)
        table.add_row("summary", str(r.ingested), "0", str(r.skipped), str(r.failed))
        _source_results.append(("summary", r))

    if run_gws:
        from manager_os.ingest.gws_client import ingest_gws_snapshots
        r = ingest_gws_snapshots(settings.gws_snapshot_dir, conn,
                                 target_date=target_date, force=force)
        table.add_row("gws", str(r.ingested), "0", str(r.skipped), str(r.failed))
        _source_results.append(("gws", r))
        if r.failed:
            had_error = True

    if run_workspace:
        r = _do_workspace_ingest(conn, settings, target_date, force=force, fetch=workspace_fetch)
        table.add_row("workspace", str(r.ingested), "0", str(r.skipped), str(r.failed))
        _source_results.append(("workspace", r))
        if r.errors:
            for err in r.errors[:10]:
                console.print(f"  [yellow]⚠ workspace:[/yellow] {err}")
        if r.failed:
            had_error = True

    console.print(table)
    _print_skip_info(_source_results, verbose)
    if had_error:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# manager-os extract (stub — implemented in Issue #11)
# ---------------------------------------------------------------------------


@app.command()
def extract(
    extract_date: Optional[str] = typer.Option(None, "--date"),
    mode: str = typer.Option("rules", "--mode", help="rules | llm | both"),
    entity: str = typer.Option("all", "--entity", help="person | client | deal | all"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show skip reason details after the results table.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview what would be extracted without writing to the database.",
    ),
    progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Show live progress output during extraction.",
    ),
    llm_limit: Optional[int] = typer.Option(
        None,
        "--llm-limit",
        help="Maximum notes to send to the LLM. 0 means unlimited. "
             "Defaults to MANAGER_OS_LLM_MAX_CANDIDATES or 25.",
    ),
    llm_timeout_seconds: Optional[int] = typer.Option(
        None,
        "--llm-timeout-seconds",
        help="Per-note LLM timeout. Defaults to MANAGER_OS_GEMINI_CLI_TIMEOUT_SECONDS or 120.",
    ),
    llm_source_path: Optional[str] = typer.Option(
        None,
        "--llm-source-path",
        help="Only send notes whose source_path contains this substring to the LLM.",
    ),
    llm_note_id: Optional[str] = typer.Option(
        None,
        "--llm-note-id",
        help="Only send the note with this exact id to the LLM.",
    ),
    llm_since_days: Optional[int] = typer.Option(
        None,
        "--llm-since-days",
        help="Only send notes newer than this many days to the LLM.",
    ),
) -> None:
    """Extract signals and action items from ingested documents."""
    import os as _os
    import time as _time

    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.extract.signals import run_rule_extraction
    from manager_os.extract.action_items import extract_action_items_from_all_notes

    valid_modes = {"rules", "llm", "both"}
    if mode not in valid_modes:
        console.print(f"[red]Unknown mode '{mode}'. Must be one of: {', '.join(sorted(valid_modes))}[/red]")
        raise typer.Exit(1)

    settings = get_settings()
    run_date = date.fromisoformat(extract_date) if extract_date else date.today()

    # Resolve LLM limit: CLI > env > default. 0 means unlimited.
    effective_llm_limit: int | None
    if llm_limit is not None:
        effective_llm_limit = llm_limit if llm_limit != 0 else None
    else:
        effective_llm_limit = int(
            _os.environ.get("MANAGER_OS_LLM_MAX_CANDIDATES", "25")
        )
    if llm_limit is not None and llm_limit == 0:
        console.print("[yellow]⚠ --llm-limit 0 selected — LLM candidates are unlimited.[/yellow]")

    if dry_run:
        _do_dry_run_extract(
            settings,
            run_date,
            mode,
            llm_limit=effective_llm_limit,
            llm_source_path=llm_source_path,
            llm_note_id=llm_note_id,
            llm_since_days=llm_since_days,
        )
        return

    conn = get_connection(settings.db_path)

    # Check that documents exist
    doc_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    if doc_count == 0:
        console.print("[red]No notes found in the database. Run 'manager-os ingest' first.[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]Extracting from {doc_count} note(s) — {run_date}[/dim]")

    table = Table(title=f"Extraction results — {run_date}", show_header=True)
    table.add_column("Step", style="cyan")
    table.add_column("Written", justify="right", style="green")
    table.add_column("Skipped", justify="right", style="yellow")
    table.add_column("Failed", justify="right", style="red")

    _step_results: list[tuple[str, object]] = []

    if mode in ("rules", "both"):
        stage_start = _time.monotonic()
        console.print("[dim]  → Running rule-based signal extraction...[/dim]") if progress else None
        result = run_rule_extraction(conn, run_date=run_date)
        if progress:
            console.print(
                f"[dim]  ← Rule extraction complete in "
                f"{_time.monotonic() - stage_start:.2f}s "
                f"(written={result.written}, skipped={result.skipped}, failed={result.failed})[/dim]"
            )
        table.add_row("signals (rules)", str(result.written), str(result.skipped), str(result.failed))
        _step_results.append(("signals (rules)", result))

    if mode in ("llm", "both"):
        from manager_os.extract.llm_signals import run_llm_extraction, LLMExtractionUnavailable
        try:
            def _progress_cb(event: str, payload: dict) -> None:
                if not progress:
                    return
                if event == "stage_start":
                    console.print(f"[dim]  → {payload.get('message', payload.get('stage'))}[/dim]")
                elif event == "candidate_start":
                    idx = payload.get("index", 0)
                    total = payload.get("total", 0)
                    path = payload.get("source_path", "")
                    console.print(f"[dim]    [{idx}/{total}] LLM candidate: {path}[/dim]")
                elif event == "stage_end" and payload.get("stage") == "llm_extraction":
                    elapsed = payload.get("elapsed_seconds", 0.0)
                    console.print(
                        f"[dim]  ← LLM extraction complete in {elapsed:.2f}s "
                        f"(written={payload.get('written')}, skipped={payload.get('skipped')}, "
                        f"failed={payload.get('failed')})[/dim]"
                    )

            llm_result = run_llm_extraction(
                conn,
                run_date=run_date,
                max_candidates=effective_llm_limit,
                timeout_seconds=llm_timeout_seconds,
                source_path_filter=llm_source_path,
                note_id=llm_note_id,
                since_days=llm_since_days,
                progress_callback=_progress_cb,
            )
            table.add_row("signals (llm)", str(llm_result.written), str(llm_result.skipped), str(llm_result.failed))
            _step_results.append(("signals (llm)", llm_result))
        except LLMExtractionUnavailable as exc:
            console.print(f"[yellow]LLM extraction skipped: {exc}[/yellow]")

    # Action items always run
    ai_result = extract_action_items_from_all_notes(conn)
    table.add_row("action items", str(ai_result.written), str(ai_result.skipped), str(ai_result.failed))
    _step_results.append(("action items", ai_result))

    # Decisions always run
    from manager_os.extract.decisions import extract_decisions_from_all_notes
    dec_result = extract_decisions_from_all_notes(conn)
    table.add_row("decisions", str(dec_result.written), str(dec_result.skipped), str(dec_result.failed))
    _step_results.append(("decisions", dec_result))

    console.print(table)
    _print_skip_info(_step_results, verbose)


# ---------------------------------------------------------------------------
# manager-os brief (stub — implemented in Issue #12)
# ---------------------------------------------------------------------------


@app.command()
def brief(
    brief_date: Optional[str] = typer.Option(None, "--date"),
    output: Optional[str] = typer.Option(None, "--output"),
    max_items: Optional[int] = typer.Option(
        None,
        "--max-items",
        help="Override the per-section item limit (default: risks=3, people=3, deals=3, follow-ups=3, meetings=5).",
    ),
    include_low_priority: bool = typer.Option(
        False,
        "--include-low-priority",
        help="Include severity='low' signals that are normally hidden.",
    ),
) -> None:
    """Generate a daily markdown brief."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.daily_brief import generate_daily_brief, write_brief_to_file

    settings = get_settings()
    target_date = date.fromisoformat(brief_date) if brief_date else date.today()
    conn = get_connection(settings.db_path)

    b = generate_daily_brief(
        conn,
        target_date=target_date,
        max_items=max_items,
        include_low_priority=include_low_priority,
    )
    out_path = write_brief_to_file(b, output_path=output)
    console.print(f"[green]Brief written to:[/green] {out_path}")
    total = len(b.signal_ids)
    shown = b.shown_signals
    if shown >= total:
        console.print(f"  Showing all {total} open signal(s).")
    else:
        console.print(f"  Showing {shown} of {total} open signals.")


# ---------------------------------------------------------------------------
# manager-os dashboard (stub — implemented in Issue #13)
# ---------------------------------------------------------------------------


@app.command()
def dashboard() -> None:
    """Launch the Streamlit dashboard."""
    import subprocess
    from pathlib import Path
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    console.print(f"[green]Launching dashboard:[/green] {app_path}")
    subprocess.run(["streamlit", "run", str(app_path)], check=False)


# ---------------------------------------------------------------------------
# manager-os daily (morning workflow)
# ---------------------------------------------------------------------------


def _resolve_daily_extract_mode(rules_only: bool, llm_only: bool) -> str:
    """Resolve daily extraction mode from flags.  Fails fast on conflict."""
    if rules_only and llm_only:
        raise typer.BadParameter(
            "Use only one of --rules-only or --llm-only.",
            param_hint="'--rules-only' / '--llm-only'",
        )
    if rules_only:
        return "rules"
    if llm_only:
        return "llm"
    return "both"


@app.command()
def daily(
    target_date: Optional[str] = typer.Option(None, "--date", help="Date (YYYY-MM-DD). Defaults to today."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing to DB or retrieving workspace."),
    no_workspace: bool = typer.Option(False, "--no-workspace", help="Skip workspace retrieval entirely."),
    rules_only: bool = typer.Option(False, "--rules-only", help="Run rule-based extraction only (no LLM)."),
    llm_only: bool = typer.Option(False, "--llm-only", help="Run LLM extraction only (skip rule extraction)."),
    llm_limit: int = typer.Option(25, "--llm-limit", help="Maximum notes to send to the LLM."),
    llm_timeout_seconds: int = typer.Option(120, "--llm-timeout-seconds", help="Per-note LLM timeout in seconds."),
    max_items: int = typer.Option(20, "--max-items", help="Maximum items per section in the brief."),
    open_dashboard: bool = typer.Option(False, "--open-dashboard", help="Launch dashboard after brief generation."),
    skip_brief: bool = typer.Option(False, "--skip-brief", help="Skip brief generation."),
    skip_extract: bool = typer.Option(False, "--skip-extract", help="Skip signal extraction."),
    skip_ingest: bool = typer.Option(False, "--skip-ingest", help="Skip all ingest steps."),
    force_ingest: bool = typer.Option(False, "--force-ingest", help="Re-ingest files even if content hash unchanged."),
    skip_forecast_fetch: bool = typer.Option(False, "--skip-forecast-fetch", help="Skip fetching forecast from Google Sheet."),
    forecast_force: bool = typer.Option(False, "--forecast-force", help="Force overwrite local forecast CSV during fetch."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed skip/warning information."),
) -> None:
    """Run the complete morning Manager OS workflow.

    Default extraction runs both rule-based and LLM extraction.
    Use --rules-only to skip LLM, or --llm-only to skip rules.
    Combining both flags is an error.

    Equivalent to running ingest, extract, brief, and optionally
    dashboard in sequence with sensible defaults.
    """
    from datetime import date as _date
    from rich import box as rich_box
    from rich.panel import Panel
    from manager_os.config import get_settings, load_source_priority
    from manager_os.db import get_connection, seed_from_config
    from manager_os.ingest.obsidian import ingest_vault
    from manager_os.ingest.forecast import ingest_forecast
    from manager_os.ingest.deals import ingest_deals
    from manager_os.ingest.workspace_summary import ingest_summary

    run_date = _date.fromisoformat(target_date) if target_date else _date.today()
    settings = get_settings()
    extract_mode = _resolve_daily_extract_mode(rules_only, llm_only)

    # Track warnings across phases for the closing summary
    extraction_warnings: list[str] = []
    brief_path: str | None = None

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    console.print()
    console.print(Panel.fit(
        "[bold]Manager OS — Daily Morning Flow[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))
    console.print(f"  [dim]Date:[/dim]          {run_date}")
    console.print(f"  [dim]DB:[/dim]            {settings.db_path}")
    console.print(f"  [dim]Vault:[/dim]         {settings.vault_path or '(not set)'}")
    console.print(f"  [dim]Extract mode:[/dim]  {extract_mode}")
    console.print(f"  [dim]LLM limit:[/dim]     {llm_limit}")
    console.print(f"  [dim]Max brief items:[/dim] {max_items}")
    console.print(f"  [dim]Workspace:[/dim]     {'disabled' if no_workspace else 'enabled'}")
    console.print(f"  [dim]Dashboard:[/dim]     {'yes' if open_dashboard else 'no'}")
    if dry_run:
        console.print(f"  [yellow bold]DRY RUN — nothing will be written.[/yellow bold]")
    console.print()

    # ------------------------------------------------------------------
    # Phase 1: Workspace fetch
    # ------------------------------------------------------------------
    workspace_results: dict[str, bool] = {}
    workspace_snapshots_found: bool = False

    if not no_workspace and not dry_run:
        try:
            import os as _os
            ws_enabled = (
                _os.environ.get("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false").lower()
                in ("true", "yes", "1")
            )
        except Exception:
            ws_enabled = False

        if ws_enabled:
            console.print("[bold]Phase 1: Workspace fetch[/bold]")
            from manager_os.ingest.workspace_gemini import (
                retrieve_forecast,
                retrieve_calendar,
                retrieve_activity,
            )

            sources = [
                ("forecast", retrieve_forecast),
                ("calendar", retrieve_calendar),
                ("activity", retrieve_activity),
            ]
            failures = 0
            for src_label, fn in sources:
                try:
                    console.print(f"[dim]  → Fetching {src_label}…[/dim]")
                    r = fn(target_date=run_date, use_yolo=True, timeout=300)
                    if r.ok:
                        console.print(f"[green]  ✓ {src_label}: {len(r.items)} item(s) → {r.written_to}[/green]")
                        workspace_results[src_label] = True
                    else:
                        console.print(f"[yellow]  ⚠ {src_label}: {r.error}[/yellow]")
                        workspace_results[src_label] = False
                        failures += 1
                except Exception as exc:
                    console.print(f"[yellow]  ⚠ {src_label}: {exc}[/yellow]")
                    workspace_results[src_label] = False
                    failures += 1

            total_sources = len(sources)
            if failures == total_sources:
                console.print("[red]All workspace sources failed.[/red]")
            elif failures > 0:
                console.print(f"[yellow]{failures}/{total_sources} workspace source(s) failed (non-fatal).[/yellow]")
            else:
                console.print("[green]All workspace sources retrieved.[/green]")

            # Check if snapshots exist on disk (even if some fetches failed)
            from manager_os.ingest.workspace_snapshot import _snapshot_exists
            workspace_snapshots_found = any(
                _snapshot_exists(subdir, run_date)
                for subdir in ("forecast", "calendar", "activity")
            )
            console.print()
        else:
            console.print(
                "[dim]Workspace retrieval disabled. Set "
                "MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED=true to enable.[/dim]"
            )
    elif not no_workspace and dry_run:
        console.print("[yellow bold]Phase 1: Workspace fetch — Skipped (dry run)[/yellow bold]")
        console.print()
    else:
        console.print("[dim]Phase 1: Workspace fetch — Skipped (--no-workspace)[/dim]")
        console.print()

    # ------------------------------------------------------------------
    # Phase 1.5: Forecast Fetch (if configured)
    # ------------------------------------------------------------------
    if not skip_ingest and not skip_forecast_fetch and not dry_run:
        src = getattr(settings, "forecast_source", "google_sheet_gemini")
        if src == "google_sheet_gemini":
            console.print("[bold]Phase 1.5: Forecast Fetch[/bold]")
            try:
                import subprocess
                import json
                import csv
                import hashlib
                from datetime import datetime
                from manager_os.llm.gemini_cli import GEMINI_CLI_BIN, GEMINI_CLI_MODEL, GEMINI_CLI_ARGS, GEMINI_CLI_TIMEOUT
                
                s_id = getattr(settings, "forecast_sheet_id", "")
                s_gid = getattr(settings, "forecast_sheet_gid", "")
                s_url = getattr(settings, "forecast_sheet_url", "")
                local_csv = getattr(settings, "forecast_local_csv", "") or getattr(settings, "forecast_csv", "")
                timeout = getattr(settings, "forecast_download_timeout_seconds", 120)

                if not s_id or not s_gid or not s_url or not local_csv:
                    raise RuntimeError("Missing required forecast sheet configuration for fetch.")

                console.print(f"[dim]  → Retrieving forecast via Gemini CLI for Sheet ID: {s_id}, GID: {s_gid}[/dim]")
                prompt = f"""You are operating in read-only mode.
Do not create, edit, delete, send, move, or modify anything.

Open this exact Google Sheet:
{s_url}

Read only the tab with gid:
{s_gid}

Return the raw tabular data from that tab only.

Do not summarize.
Do not infer.
Do not search Drive.
Do not choose a different spreadsheet.
Do not transform business semantics.
Do not omit blank cells.

Return strict JSON only with:
{{
  "ok": true,
  "source": "google_sheet_forecast",
  "source_url": "{s_url}",
  "sheet_id": "{s_id}",
  "gid": "{s_gid}",
  "retrieved_at": "...",
  "rows": [
    ["cell A1", "cell B1", "..."],
    ["cell A2", "cell B2", "..."]
  ]
}}

If you cannot access the sheet, return:
{{
  "ok": false,
  "source": "google_sheet_forecast",
  "sheet_id": "{s_id}",
  "gid": "{s_gid}",
  "error": "..."
}}"""
                
                cmd = [GEMINI_CLI_BIN]
                if GEMINI_CLI_MODEL:
                    cmd.extend(["--model", GEMINI_CLI_MODEL])
                if GEMINI_CLI_ARGS:
                    cmd.extend(GEMINI_CLI_ARGS.split())
                cmd.append("-y")
                
                effective_timeout = timeout if timeout else GEMINI_CLI_TIMEOUT
                proc = subprocess.run(
                    cmd + ["--prompt", prompt],
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                )
                
                if proc.returncode != 0:
                    raise RuntimeError(f"Gemini CLI failed: {proc.stderr}")
                    
                output_text = proc.stdout.strip()
                if output_text.startswith("```json"):
                    output_text = output_text[7:]
                if output_text.endswith("```"):
                    output_text = output_text[:-3]
                    
                result_data = json.loads(output_text)
                
                if not result_data.get("ok"):
                    raise RuntimeError(f"Gemini CLI reported failure: {result_data.get('error', 'Unknown error')}")
                    
                rows = result_data.get("rows", [])
                if not rows:
                    raise RuntimeError("Gemini CLI returned no rows.")
                    
                Path(local_csv).parent.mkdir(parents=True, exist_ok=True)
                csv_content = ""
                with open(local_csv, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)
                    f.seek(0)
                    csv_content = f.read()
                    
                content_hash = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()
                retrieved_at = result_data.get("retrieved_at", datetime.utcnow().isoformat())
                
                meta_path = f"{local_csv}.meta.json"
                metadata = {
                    "source": "google_sheet_gemini",
                    "sheet_url": s_url,
                    "sheet_id": s_id,
                    "gid": s_gid,
                    "retrieved_at": retrieved_at,
                    "local_csv_path": local_csv,
                    "row_count": len(rows),
                    "content_hash": content_hash
                }
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)
                    
                console.print(f"[green]  ✓ Forecast retrieved and saved to {local_csv}[/green]")

            except subprocess.TimeoutExpired:
                console.print(f"[red]  ✗ Forecast fetch timed out after {timeout} seconds.[/red]")
                console.print("[red]  ✗ Failing daily run because exact source is mandatory.[/red]")
                raise typer.Exit(1)
            except Exception as e:
                console.print(f"[red]  ✗ Forecast fetch failed: {e}[/red]")
                console.print("[red]  ✗ Failing daily run because exact source is mandatory.[/red]")
                raise typer.Exit(1)
            console.print()

    # ------------------------------------------------------------------
    # Phase 2: Ingest
    # ------------------------------------------------------------------
    if skip_ingest:
        console.print("[dim]Phase 2: Ingest — Skipped (--skip-ingest)[/dim]")
        console.print()
    elif dry_run:
        console.print("[yellow bold]Phase 2: Ingest — Dry-run preview[/yellow bold]")
        _do_dry_run_ingest("all", run_date, settings)
    else:
        console.print("[bold]Phase 2: Ingest[/bold]")

        try:
            sp = load_source_priority(settings)
        except Exception:
            sp = None

        conn = get_connection(settings.db_path)

        # Seed from config
        try:
            seeded = seed_from_config(conn, settings)
            if seeded["people"] or seeded["clients"]:
                console.print(
                    f"[dim]Seeded from config: {seeded['people']} people, "
                    f"{seeded['clients']} clients[/dim]"
                )
        except Exception:
            pass

        table = Table(title=f"Ingest — {run_date}", show_header=True)
        table.add_column("Source", style="cyan")
        table.add_column("Ingested", justify="right", style="green")
        table.add_column("Warn", justify="right", style="yellow")
        table.add_column("Skipped", justify="right", style="dim")
        table.add_column("Failed", justify="right", style="red")

        had_error = False
        _source_results: list[tuple[str, object]] = []

        # Obsidian
        if settings.vault_path:
            try:
                r = ingest_vault(settings.vault_path, conn, force=force_ingest)
                table.add_row("obsidian", str(r.ingested), str(r.ingested_with_warnings),
                               str(r.skipped), str(r.failed))
                _source_results.append(("obsidian", r))
                if r.ingested_with_warnings:
                    for w in r.warnings[:10]:
                        console.print(f"  [yellow]⚠ frontmatter:[/yellow] {w}")
                if r.failed:
                    had_error = True
            except FileNotFoundError as exc:
                console.print(f"[red]Vault not found: {exc}[/red]")
                had_error = True
        else:
            console.print("[red]MANAGER_OS_VAULT_PATH is not set.[/red]")

        # Forecast CSV
        try:
            r = ingest_forecast(settings.forecast_csv, conn, source_priority=sp, force=force_ingest)
            table.add_row("forecast", str(r.ingested), "0", str(r.skipped), str(r.failed))
            _source_results.append(("forecast", r))
            if r.failed:
                had_error = True
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[red]Forecast CSV error: {exc}[/red]")
            had_error = True

        # Deals CSV
        try:
            r = ingest_deals(settings.deals_csv, conn, source_priority=sp, force=force_ingest)
            table.add_row("deals", str(r.ingested), "0", str(r.skipped), str(r.failed))
            _source_results.append(("deals", r))
            if r.failed:
                had_error = True
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[red]Deals CSV error: {exc}[/red]")
            had_error = True

        # Workspace summaries
        r = ingest_summary(settings.workspace_summary_dir, run_date, conn, force=force_ingest)
        table.add_row("summary", str(r.ingested), "0", str(r.skipped), str(r.failed))
        _source_results.append(("summary", r))

        # GWS snapshots
        from manager_os.ingest.gws_client import ingest_gws_snapshots
        r = ingest_gws_snapshots(settings.gws_snapshot_dir, conn,
                                 target_date=run_date, force=force_ingest)
        table.add_row("gws", str(r.ingested), "0", str(r.skipped), str(r.failed))
        _source_results.append(("gws", r))
        if r.failed:
            had_error = True

        # Workspace snapshots (if any exist)
        if workspace_snapshots_found or workspace_results:
            try:
                r = _do_workspace_ingest(conn, settings, run_date, force=force_ingest, fetch=False)
                table.add_row("workspace", str(r.ingested), "0", str(r.skipped), str(r.failed))
                _source_results.append(("workspace", r))
                if r.errors:
                    for err in r.errors[:10]:
                        console.print(f"  [yellow]⚠ workspace:[/yellow] {err}")
                if r.failed:
                    had_error = True
            except Exception as exc:
                console.print(f"[yellow]Workspace ingest skipped: {exc}[/yellow]")

        console.print(table)
        _print_skip_info(_source_results, verbose)
        if had_error:
            console.print("[yellow]Some ingest sources had errors (non-fatal).[/yellow]")
        console.print()

        conn.close()

    # ------------------------------------------------------------------
    # Phase 3: Extract
    # ------------------------------------------------------------------
    if skip_extract:
        console.print("[dim]Phase 3: Extract — Skipped (--skip-extract)[/dim]")
        console.print()
    elif dry_run:
        console.print("[yellow bold]Phase 3: Extract — Dry-run preview[/yellow bold]")
        _do_dry_run_extract(settings, run_date, extract_mode,
                           llm_limit=llm_limit)
    else:
        console.print("[bold]Phase 3: Extract[/bold]")
        import time as _time
        import os as _os

        conn = get_connection(settings.db_path)

        doc_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        if doc_count == 0:
            console.print("[red]No notes found. Run ingest first.[/red]")
            conn.close()
            raise typer.Exit(1)

        console.print(f"[dim]Extracting from {doc_count} note(s) — {run_date}[/dim]")

        table = Table(title=f"Extraction — {run_date}", show_header=True)
        table.add_column("Step", style="cyan")
        table.add_column("Written", justify="right", style="green")
        table.add_column("Skipped", justify="right", style="yellow")
        table.add_column("Failed", justify="right", style="red")

        _step_results: list[tuple[str, object]] = []
        extraction_warnings: list[str] = []

        # Rule extraction (modes: rules, both)
        if extract_mode in ("rules", "both"):
            from manager_os.extract.signals import run_rule_extraction
            stage_start = _time.monotonic()
            console.print("[dim]  → Running rule-based signal extraction…[/dim]")
            rule_result = run_rule_extraction(conn, run_date=run_date)
            console.print(
                f"[dim]  ← Rules complete in {_time.monotonic() - stage_start:.2f}s "
                f"(written={rule_result.written}, skipped={rule_result.skipped}, "
                f"failed={rule_result.failed})[/dim]"
            )
            table.add_row("signals (rules)", str(rule_result.written),
                           str(rule_result.skipped), str(rule_result.failed))
            _step_results.append(("signals (rules)", rule_result))
        else:
            console.print("[dim]  → Rule extraction skipped (--llm-only)[/dim]")

        # LLM extraction (modes: llm, both)
        if extract_mode in ("llm", "both"):
            from manager_os.extract.llm_signals import run_llm_extraction, LLMExtractionUnavailable
            try:
                def _progress_cb(event: str, payload: dict) -> None:
                    if event == "stage_start":
                        console.print(f"[dim]  → {payload.get('message', payload.get('stage'))}[/dim]")
                    elif event == "candidate_start":
                        idx = payload.get("index", 0)
                        total = payload.get("total", 0)
                        path = payload.get("source_path", "")
                        console.print(f"[dim]    [{idx}/{total}] LLM candidate: {path}[/dim]")
                    elif event == "stage_end" and payload.get("stage") == "llm_extraction":
                        elapsed = payload.get("elapsed_seconds", 0.0)
                        console.print(
                            f"[dim]  ← LLM extraction complete in {elapsed:.2f}s "
                            f"(written={payload.get('written')}, skipped={payload.get('skipped')}, "
                            f"failed={payload.get('failed')})[/dim]"
                        )

                effective_limit = llm_limit if llm_limit > 0 else None
                llm_result = run_llm_extraction(
                    conn, run_date=run_date, max_candidates=effective_limit,
                    timeout_seconds=llm_timeout_seconds,
                    progress_callback=_progress_cb,
                )
                table.add_row("signals (llm)", str(llm_result.written),
                               str(llm_result.skipped), str(llm_result.failed))
                _step_results.append(("signals (llm)", llm_result))
            except LLMExtractionUnavailable as exc:
                if extract_mode == "llm":
                    # --llm-only: fail loudly — user explicitly requested LLM
                    console.print(
                        f"[red bold]LLM extraction unavailable (--llm-only run): {exc}[/red bold]"
                    )
                    console.print(
                        "[red]Gemini CLI / LLM is not configured or reachable. "
                        "Run 'manager-os llm-doctor' to diagnose.[/red]"
                    )
                    conn.close()
                    raise typer.Exit(1)
                else:
                    # default both: warn and continue (existing behavior)
                    console.print(f"[yellow]LLM extraction skipped: {exc}[/yellow]")
                    extraction_warnings.append(f"LLM: {exc}")

        # Action items
        from manager_os.extract.action_items import extract_action_items_from_all_notes
        ai_result = extract_action_items_from_all_notes(conn)
        table.add_row("action items", str(ai_result.written),
                       str(ai_result.skipped), str(ai_result.failed))
        _step_results.append(("action items", ai_result))

        # Decisions
        from manager_os.extract.decisions import extract_decisions_from_all_notes
        dec_result = extract_decisions_from_all_notes(conn)
        table.add_row("decisions", str(dec_result.written),
                       str(dec_result.skipped), str(dec_result.failed))
        _step_results.append(("decisions", dec_result))

        console.print(table)
        _print_skip_info(_step_results, verbose)
        if extraction_warnings:
            for w in extraction_warnings:
                console.print(f"[yellow]  ⚠ {w}[/yellow]")
        console.print()

        conn.close()

    # ------------------------------------------------------------------
    # Phase 4: Brief
    # ------------------------------------------------------------------
    brief_path: str | None = None
    if skip_brief:
        console.print("[dim]Phase 4: Brief — Skipped (--skip-brief)[/dim]")
        console.print()
    elif dry_run:
        console.print("[yellow bold]Phase 4: Brief — Skipped (dry run)[/yellow bold]")
        console.print()
    else:
        console.print("[bold]Phase 4: Brief[/bold]")
        from manager_os.db import get_connection as _get_conn
        from manager_os.build.daily_brief import generate_daily_brief, write_brief_to_file

        conn = _get_conn(settings.db_path)
        b = generate_daily_brief(conn, target_date=run_date, max_items=max_items)
        out_path = write_brief_to_file(b)
        brief_path = str(out_path)
        console.print(f"[green]Brief written to:[/green] {out_path}")
        total = len(b.signal_ids)
        shown = b.shown_signals
        if shown >= total:
            console.print(f"  Showing all {total} open signal(s).")
        else:
            console.print(f"  Showing {shown} of {total} open signals.")
        console.print()
        conn.close()

    # ------------------------------------------------------------------
    # Phase 5: Dashboard (optional)
    # ------------------------------------------------------------------
    if open_dashboard and not dry_run:
        console.print("[bold]Phase 5: Dashboard[/bold]")
        import subprocess
        app_path = Path(__file__).parent / "dashboard" / "app.py"
        console.print(f"[green]Launching dashboard:[/green] {app_path}")
        subprocess.run(["streamlit", "run", str(app_path)], check=False)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    console.print()
    console.print(Panel.fit(
        "[bold]Daily Flow Complete[/bold]",
        box=rich_box.ROUNDED,
        border_style="green",
    ))
    summary_lines: list[str] = []

    # Workspace
    if not no_workspace and not dry_run and workspace_results:
        ws_ok = sum(1 for v in workspace_results.values() if v)
        ws_total = len(workspace_results)
        summary_lines.append(f"Workspace: {ws_ok}/{ws_total} source(s) retrieved")
    elif no_workspace:
        summary_lines.append("Workspace: skipped (--no-workspace)")
    elif dry_run:
        summary_lines.append("Workspace: skipped (dry run)")

    if not skip_ingest:
        summary_lines.append("Ingest: complete")
    else:
        summary_lines.append("Ingest: skipped")

    if not skip_extract:
        summary_lines.append(f"Extract: {extract_mode} mode")
    else:
        summary_lines.append("Extract: skipped")

    if brief_path:
        summary_lines.append(f"Brief: {brief_path}")
    elif skip_brief:
        summary_lines.append("Brief: skipped")
    elif dry_run:
        summary_lines.append("Brief: would be generated")

    if extraction_warnings:
        summary_lines.append(f"Warnings: {'; '.join(extraction_warnings)}")
    if dry_run:
        summary_lines.append("[yellow]DRY RUN — no data was written.[/yellow]")

    for line in summary_lines:
        console.print(f"  • {line}")
    console.print()


# ---------------------------------------------------------------------------
# manager-os meeting-prep (stub — implemented in Issue #18)
# ---------------------------------------------------------------------------


@app.command(name="meeting-prep")
def meeting_prep(
    prep_date: Optional[str] = typer.Option(None, "--date"),
    meeting: Optional[str] = typer.Option(None, "--meeting", help="Meeting title slug to match"),
    llm: bool = typer.Option(False, "--llm", help="Enrich with LLM synthesis (requires OPENAI_API_KEY)"),
) -> None:
    """Generate meeting prep documents for today's meetings."""
    from manager_os.config import get_settings, load_clients, load_deal_aliases, load_people
    from manager_os.db import get_connection
    from manager_os.extract.entities import EntityResolver
    from manager_os.extract.meeting_prep import (
        generate_meeting_prep,
        enrich_meeting_prep_with_llm,
        write_meeting_prep_to_file,
    )
    from manager_os.schemas import MeetingRecord
    import json

    settings = get_settings()
    target_date = date.fromisoformat(prep_date) if prep_date else date.today()
    conn = get_connection(settings.db_path)

    try:
        resolver = EntityResolver(load_people(settings), load_clients(settings), load_deal_aliases(settings))
    except Exception:
        resolver = None

    # Fetch meetings for the date
    rows = conn.execute(
        "SELECT id, start_time, title, attendees, linked_entities, source, external_id, updated_at "
        "FROM meetings WHERE meeting_date = ? ORDER BY start_time NULLS LAST",
        [target_date],
    ).fetchall()

    if not rows:
        console.print(f"[yellow]No meetings found for {target_date}. Add them to the meetings table or ingest from calendar.[/yellow]")
        raise typer.Exit(0)

    for row in rows:
        mtg = MeetingRecord(
            id=row[0], meeting_date=target_date, start_time=row[1] or "",
            title=row[2],
            attendees=json.loads(row[3]) if row[3] else [],
            linked_entities=json.loads(row[4]) if row[4] else [],
            source=row[5] or "", external_id=row[6] or "",
        )

        # Filter by slug if provided
        if meeting and meeting.lower() not in mtg.title.lower().replace(" ", "-"):
            continue

        prep = generate_meeting_prep(mtg, conn, resolver)
        if llm:
            prep = enrich_meeting_prep_with_llm(prep, conn)
        out = write_meeting_prep_to_file(prep, mtg.title, target_date)
        console.print(f"[green]Meeting prep written:[/green] {out.name}")
        console.print(f"  {mtg.title} ({target_date})")


# ---------------------------------------------------------------------------
# manager-os meeting-prep-preview
# ---------------------------------------------------------------------------


@app.command(name="meeting-prep-preview")
def meeting_prep_preview(
    preview_date: Optional[str] = typer.Option(None, "--date"),
    meeting_id: Optional[str] = typer.Option(None, "--meeting-id", help="Meeting ID to preview"),
    no_llm: bool = typer.Option(True, "--no-llm", help="Skip LLM enrichment (default)"),
    llm: bool = typer.Option(False, "--llm", help="Enable LLM enrichment (requires Gemini CLI)"),
    print_context: bool = typer.Option(False, "--print-context", help="Print scored context candidates"),
) -> None:
    """Preview meeting prep context scoring without generating full prep."""
    from manager_os.config import get_settings, load_clients, load_deal_aliases, load_people
    from manager_os.db import get_connection
    from manager_os.extract.entities import EntityResolver
    from manager_os.extract.meeting_prep import get_relevant_meeting_context
    from manager_os.schemas import MeetingRecord
    import json

    settings = get_settings()
    target_date = date.fromisoformat(preview_date) if preview_date else date.today()
    conn = get_connection(settings.db_path)

    try:
        resolver = EntityResolver(load_people(settings), load_clients(settings), load_deal_aliases(settings))
    except Exception:
        resolver = None

    # Fetch meeting
    if meeting_id:
        row = conn.execute(
            "SELECT id, start_time, title, attendees, linked_entities, source, external_id, updated_at "
            "FROM meetings WHERE id = ?",
            [meeting_id],
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, start_time, title, attendees, linked_entities, source, external_id, updated_at "
            "FROM meetings WHERE meeting_date = ? ORDER BY start_time NULLS LAST LIMIT 1",
            [target_date],
        ).fetchone()

    if not row:
        console.print(f"[yellow]No meeting found.[/yellow]")
        raise typer.Exit(0)

    mtg = MeetingRecord(
        id=row[0], meeting_date=target_date, start_time=row[1] or "",
        title=row[2],
        attendees=json.loads(row[3]) if row[3] else [],
        linked_entities=json.loads(row[4]) if row[4] else [],
        source=row[5] or "", external_id=row[6] or "",
    )

    console.print(f"[bold]Meeting:[/bold] {mtg.title}")
    console.print(f"[bold]Date:[/bold] {mtg.meeting_date}")
    console.print(f"[bold]Attendees:[/bold] {', '.join(mtg.attendees) or 'None'}")
    console.print(f"[bold]Linked Entities:[/bold] {mtg.linked_entities or 'None'}")
    console.print()

    # Get scored context
    candidates = get_relevant_meeting_context(mtg, conn, resolver, limit=10)

    if print_context:
        console.print("[bold]Scored Context Candidates:[/bold]")
        for i, c in enumerate(candidates, 1):
            console.print(f"  {i}. [cyan]{c.title}[/cyan] (score: {c.score:.0f})")
            console.print(f"     Source: {c.source_type} — {c.source_path or 'N/A'}")
            console.print(f"     Entity: {c.entity_type}:{c.entity_name}")
            console.print(f"     Reasons: {', '.join(c.reasons)}")
            console.print(f"     Excerpt: {c.excerpt[:150]}...")
            console.print()
    else:
        console.print(f"[green]Found {len(candidates)} context candidates.[/green]")
        console.print("Use --print-context to see details.")

    if llm and not no_llm:
        console.print("[yellow]LLM enrichment requested but not implemented in preview mode.[/yellow]")


# ---------------------------------------------------------------------------
# manager-os closeout (stub — implemented in Issue #23)
# ---------------------------------------------------------------------------


@app.command()
def closeout(
    closeout_date: Optional[str] = typer.Option(None, "--date"),
    weekly: Optional[bool] = typer.Option(None, "--weekly/--no-weekly",
                                           help="Force weekly exec update (default: auto on Fridays)"),
    output: Optional[str] = typer.Option(None, "--output", help="Output directory"),
) -> None:
    """Generate end-of-day closeout summary and optional weekly exec update."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.closeout import generate_closeout, write_closeout_to_file

    settings = get_settings()
    target_date = date.fromisoformat(closeout_date) if closeout_date else date.today()
    conn = get_connection(settings.db_path)

    result = generate_closeout(conn, target_date=target_date, include_weekly=weekly)
    out_path = write_closeout_to_file(result, target_date, output_dir=output)

    console.print(f"[green]Closeout written:[/green] {out_path.name}")
    console.print(f"  Signals new={result.stats.new_today}  "
                  f"resolved={result.stats.resolved_today}  "
                  f"still_open={result.stats.still_open}")
    console.print(f"  Actions open={result.stats.action_items_open}  "
                  f"closed={result.stats.action_items_closed}")

    if result.weekly_exec_content:
        weekly_path = out_path.parent / f"{target_date.isoformat()}-weekly-exec.md"
        console.print(f"[green]Weekly exec update:[/green] {weekly_path.name}")


# ---------------------------------------------------------------------------
# manager-os forecast-fetch
# ---------------------------------------------------------------------------


@app.command(name="forecast-fetch")
def forecast_fetch(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print resolved config without downloading."),
    force: bool = typer.Option(False, "--force", help="Overwrite the local forecast CSV."),
    print_url: bool = typer.Option(False, "--print-url", help="Print the deterministic CSV export URL and exit."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Print the Gemini CLI prompt and exit."),
    output: Optional[str] = typer.Option(None, "--output", help="Override the local CSV output path."),
    sheet_url: Optional[str] = typer.Option(None, "--sheet-url", help="Override the Google Sheet URL."),
    sheet_id: Optional[str] = typer.Option(None, "--sheet-id", help="Override the Google Sheet ID."),
    gid: Optional[str] = typer.Option(None, "--gid", help="Override the Google Sheet GID."),
    timeout: int = typer.Option(120, "--timeout", help="Download timeout in seconds."),
) -> None:
    """Fetch the forecast CSV from the configured Google Sheet via Gemini CLI.

    Uses deterministic retrieval. No fallbacks allowed.
    """
    import subprocess
    import json
    import csv
    import hashlib
    from datetime import datetime
    from manager_os.config import get_settings

    settings = get_settings()

    # Resolve config
    src = getattr(settings, "forecast_source", "google_sheet_gemini")
    s_id = sheet_id or getattr(settings, "forecast_sheet_id", "")
    s_gid = gid or getattr(settings, "forecast_sheet_gid", "")
    s_url = sheet_url or getattr(settings, "forecast_sheet_url", "")
    local_csv = output or getattr(settings, "forecast_local_csv", "") or getattr(settings, "forecast_csv", "")

    if print_url:
        export_url = getattr(settings, "forecast_export_url", "")
        if not export_url and s_id and s_gid:
            export_url = f"https://docs.google.com/spreadsheets/d/{s_id}/export?format=csv&gid={s_gid}"
        console.print(f"[bold]Export URL:[/bold] {export_url}")
        raise typer.Exit(0)

    if dry_run:
        console.print("[bold]Forecast Fetch — Dry Run[/bold]")
        console.print(f"  Source:       {src}")
        console.print(f"  Sheet ID:     {s_id}")
        console.print(f"  GID:          {s_gid}")
        console.print(f"  Sheet URL:    {s_url}")
        console.print(f"  Output Path:  {local_csv}")
        raise typer.Exit(0)

    if src != "google_sheet_gemini":
        console.print(f"[red]MANAGER_OS_FORECAST_SOURCE must be 'google_sheet_gemini'. Got '{src}'.[/red]")
        raise typer.Exit(1)

    if not s_id or not s_gid or not s_url:
        console.print("[red]Missing Sheet ID, GID, or URL. Check MANAGER_OS_FORECAST_SHEET_ID, GID, and URL.[/red]")
        raise typer.Exit(1)

    if not local_csv:
        console.print("[red]No local CSV path configured (MANAGER_OS_FORECAST_LOCAL_CSV).[/red]")
        raise typer.Exit(1)

    prompt = f"""You are operating in read-only mode.
Do not create, edit, delete, send, move, or modify anything.

Open this exact Google Sheet:
{s_url}

Read only the tab with gid:
{s_gid}

Return the raw tabular data from that tab only.

Do not summarize.
Do not infer.
Do not search Drive.
Do not choose a different spreadsheet.
Do not transform business semantics.
Do not omit blank cells.

Return strict JSON only with:
{{
  "ok": true,
  "source": "google_sheet_forecast",
  "source_url": "{s_url}",
  "sheet_id": "{s_id}",
  "gid": "{s_gid}",
  "retrieved_at": "...",
  "rows": [
    ["cell A1", "cell B1", "..."],
    ["cell A2", "cell B2", "..."]
  ]
}}

If you cannot access the sheet, return:
{{
  "ok": false,
  "source": "google_sheet_forecast",
  "sheet_id": "{s_id}",
  "gid": "{s_gid}",
  "error": "..."
}}"""

    if print_prompt:
        console.print("[bold]Gemini CLI Prompt:[/bold]")
        console.print(prompt)
        raise typer.Exit(0)

    console.print(f"[dim]Retrieving forecast via Gemini CLI for Sheet ID: {s_id}, GID: {s_gid}[/dim]")
    console.print(f"[dim]Saving to:[/dim] {local_csv}")

    try:
        from manager_os.llm.gemini_cli import GEMINI_CLI_BIN, GEMINI_CLI_MODEL, GEMINI_CLI_ARGS, GEMINI_CLI_TIMEOUT
        
        cmd = [GEMINI_CLI_BIN]
        if GEMINI_CLI_MODEL:
            cmd.extend(["--model", GEMINI_CLI_MODEL])
        if GEMINI_CLI_ARGS:
            cmd.extend(GEMINI_CLI_ARGS.split())
        cmd.append("-y")
        
        effective_timeout = timeout if timeout else GEMINI_CLI_TIMEOUT
        
        proc = subprocess.run(
            cmd + ["--prompt", prompt],
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        
        if proc.returncode != 0:
            raise RuntimeError(f"Gemini CLI failed: {proc.stderr}")
            
        output_text = proc.stdout.strip()
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]
            
        result_data = json.loads(output_text)
        
        if not result_data.get("ok"):
            raise RuntimeError(f"Gemini CLI reported failure: {result_data.get('error', 'Unknown error')}")
            
        rows = result_data.get("rows", [])
        if not rows:
            raise RuntimeError("Gemini CLI returned no rows.")
            
        # Write to CSV
        Path(local_csv).parent.mkdir(parents=True, exist_ok=True)
        csv_content = ""
        with open(local_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
            # Re-read to get exact content for hash
            f.seek(0)
            csv_content = f.read()
            
        content_hash = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()
        retrieved_at = result_data.get("retrieved_at", datetime.utcnow().isoformat())
        
        # Write metadata
        meta_path = f"{local_csv}.meta.json"
        metadata = {
            "source": "google_sheet_gemini",
            "sheet_url": s_url,
            "sheet_id": s_id,
            "gid": s_gid,
            "retrieved_at": retrieved_at,
            "local_csv_path": local_csv,
            "row_count": len(rows),
            "content_hash": content_hash
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
            
        console.print(f"[green]✓ Successfully retrieved and saved forecast to {local_csv}[/green]")
        console.print(f"[dim]  Metadata written to: {meta_path}[/dim]")
        
    except subprocess.TimeoutExpired:
        console.print(f"[red]✗ Gemini CLI timed out after {timeout} seconds.[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Retrieval failed: {e}[/red]")
        console.print(f"  Sheet ID: {s_id}")
        console.print(f"  GID: {s_gid}")
        console.print(f"  Sheet URL: {s_url}")
        console.print("[yellow]Suggestion: Verify your Google account has access to this sheet and Gemini CLI is configured correctly.[/yellow]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# manager-os demo-reset
# ---------------------------------------------------------------------------

# Project root derived from this file's location (src/manager_os/cli.py → 3 levels up)
_REPO_ROOT = Path(__file__).parent.parent.parent

# Keywords that mark a path as demo/fixture data — safe to treat as source.
# Deliberately excludes "test" and "tmp" because pytest temp dirs often contain them.
_SAFE_PATH_KEYWORDS = frozenset({"fixture", "demo", "sample", "mock", "fake"})


def _within_repo(p: Path) -> bool:
    """Return True if *p* is located inside the project repository root."""
    try:
        p.resolve().relative_to(_REPO_ROOT.resolve())
        return True
    except ValueError:
        return False


def _path_has_safe_keyword(p: Path) -> bool:
    """Return True if the full path string contains a known-safe keyword."""
    path_lower = str(p).lower()
    return any(kw in path_lower for kw in _SAFE_PATH_KEYWORDS)


def _source_path_looks_real(p: Path, *, is_vault: bool = False) -> bool:
    """Return True when a source path appears to contain real user data.

    A path is considered *real* when it:
    - exists on disk, AND
    - is NOT inside the project repository root, AND
    - does NOT contain a known demo/fixture keyword in its string, AND
    - for vaults: has an ``.obsidian`` subdirectory (positive Obsidian fingerprint)
    - for other paths: exists and is outside the repo (conservative)
    """
    if not p.exists():
        return False
    if _within_repo(p):
        return False
    if _path_has_safe_keyword(p):
        return False
    if is_vault:
        # Only flag as a real vault if the Obsidian marker directory is present.
        return (p / ".obsidian").is_dir()
    # Non-vault path exists outside repo with no safe keywords → assume real.
    return True


@app.command(name="demo-reset")
def demo_reset(
    demo_date: Optional[str] = typer.Option(
        None,
        "--date",
        help="Target date for ingest/extract/brief/closeout (YYYY-MM-DD). Defaults to today.",
    ),
    yes_demo: bool = typer.Option(
        False,
        "--yes-demo",
        help="Bypass safety check when source paths look like real user data.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what would be done without modifying any files.",
    ),
) -> None:
    """Reset the demo database and regenerate sample artifacts.

    Deletes and recreates a dedicated demo database
    (data/demo/manager_os_demo.duckdb), clears the demo output directory
    (output/demo/), then runs the full ingest → extract → brief → closeout
    pipeline using the source paths configured in your .env.

    The command refuses to run when any source path looks like real user data
    (e.g. an Obsidian vault outside the project with a .obsidian directory)
    unless you confirm with --yes-demo.

    Safe to run repeatedly.  Use --dry-run to preview the plan first.
    """
    import shutil

    from manager_os.config import get_settings, load_source_priority
    from manager_os.db import get_connection, seed_from_config
    from manager_os.ingest.obsidian import ingest_vault
    from manager_os.ingest.forecast import ingest_forecast
    from manager_os.ingest.deals import ingest_deals
    from manager_os.ingest.workspace_summary import ingest_summary
    from manager_os.extract.signals import run_rule_extraction
    from manager_os.extract.action_items import extract_action_items_from_all_notes
    from manager_os.extract.decisions import extract_decisions_from_all_notes
    from manager_os.build.daily_brief import generate_daily_brief, write_brief_to_file
    from manager_os.build.closeout import generate_closeout, write_closeout_to_file

    settings = get_settings()
    target_date = date.fromisoformat(demo_date) if demo_date else date.today()

    # Fixed demo-specific paths — these are always safe to wipe; they are
    # never the production DB or production output directories.
    demo_db_path = _REPO_ROOT / "data" / "demo" / "manager_os_demo.duckdb"
    demo_output_dir = _REPO_ROOT / "output" / "demo"
    demo_brief_path = demo_output_dir / f"{target_date.isoformat()}-brief.md"
    demo_closeout_dir = demo_output_dir / "closeout"

    # ------------------------------------------------------------------
    # Safety check — inspect SOURCE paths (we never delete these)
    # ------------------------------------------------------------------
    real_paths: list[str] = []

    if settings.vault_path:
        vault_p = Path(settings.vault_path)
        if _source_path_looks_real(vault_p, is_vault=True):
            real_paths.append(f"vault_path: {vault_p}")

    for label, path_str in [
        ("forecast_csv", settings.forecast_csv),
        ("deals_csv", settings.deals_csv),
        ("workspace_summary_dir", settings.workspace_summary_dir),
    ]:
        if path_str:
            p = Path(path_str)
            if _source_path_looks_real(p):
                real_paths.append(f"{label}: {p}")

    if real_paths and not yes_demo:
        console.print("[red bold]Safety check failed.[/red bold]")
        console.print(
            "The following configured source paths look like real user data:\n"
        )
        for rp in real_paths:
            console.print(f"  [yellow]• {rp}[/yellow]")
        console.print()
        console.print(
            "demo-reset only deletes the demo database and demo output directory —\n"
            "it never touches your vault or CSV source files.\n"
            "However, to avoid ingesting private data into the demo database,\n"
            "re-run with [bold]--yes-demo[/bold] to confirm you want to proceed."
        )
        raise typer.Exit(1)

    # ------------------------------------------------------------------
    # Dry-run: print the plan and exit without changing anything
    # ------------------------------------------------------------------
    if dry_run:
        console.print("[bold cyan]demo-reset dry run[/bold cyan] — no changes will be made\n")
        console.print(f"  Would delete DB:      {demo_db_path}")
        console.print(f"  Would clear output:   {demo_output_dir}/")
        console.print(f"  Date:                 {target_date}")
        console.print(f"  Sources:")
        if settings.vault_path:
            console.print(f"    vault:            {settings.vault_path}")
        console.print(f"    forecast:         {settings.forecast_csv}")
        console.print(f"    deals:            {settings.deals_csv}")
        console.print(f"    summaries:        {settings.workspace_summary_dir}")
        console.print(f"  Would write brief:    {demo_brief_path}")
        console.print(
            f"  Would write closeout: {demo_closeout_dir / (target_date.isoformat() + '.md')}"
        )
        raise typer.Exit(0)

    # ------------------------------------------------------------------
    # Reset: delete demo DB and clear demo output directory
    # ------------------------------------------------------------------
    if demo_db_path.exists():
        demo_db_path.unlink()
        console.print(f"[dim]Deleted demo database: {demo_db_path}[/dim]")
    demo_db_path.parent.mkdir(parents=True, exist_ok=True)

    if demo_output_dir.exists():
        shutil.rmtree(demo_output_dir)
        console.print(f"[dim]Cleared demo output: {demo_output_dir}[/dim]")
    demo_output_dir.mkdir(parents=True, exist_ok=True)
    demo_closeout_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Pipeline: ingest
    # ------------------------------------------------------------------
    console.print("\n[cyan]── Ingest[/cyan]")
    conn = get_connection(str(demo_db_path))

    try:
        sp = load_source_priority(settings)
    except Exception:
        sp = None

    seeded = seed_from_config(conn, settings)
    if seeded["people"] or seeded["clients"]:
        console.print(
            f"[dim]Seeded from config: {seeded['people']} people, {seeded['clients']} clients[/dim]"
        )

    ing_table = Table(show_header=True)
    ing_table.add_column("Source", style="cyan")
    ing_table.add_column("Ingested", justify="right", style="green")
    ing_table.add_column("Warn", justify="right", style="yellow")
    ing_table.add_column("Skipped", justify="right", style="dim")
    ing_table.add_column("Failed", justify="right", style="red")

    if settings.vault_path:
        try:
            r = ingest_vault(settings.vault_path, conn, force=True)
            ing_table.add_row("obsidian", str(r.ingested), str(r.ingested_with_warnings), str(r.skipped), str(r.failed))
        except FileNotFoundError as exc:
            console.print(f"[yellow]Vault not found, skipping: {exc}[/yellow]")

    try:
        r = ingest_forecast(settings.forecast_csv, conn, source_priority=sp, force=True)
        ing_table.add_row("forecast", str(r.ingested), "0", str(r.skipped), str(r.failed))
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[yellow]Forecast CSV not found, skipping: {exc}[/yellow]")

    try:
        r = ingest_deals(settings.deals_csv, conn, source_priority=sp, force=True)
        ing_table.add_row("deals", str(r.ingested), "0", str(r.skipped), str(r.failed))
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[yellow]Deals CSV not found, skipping: {exc}[/yellow]")

    r = ingest_summary(settings.workspace_summary_dir, target_date, conn, force=True)
    ing_table.add_row("summary", str(r.ingested), "0", str(r.skipped), str(r.failed))

    console.print(ing_table)

    # ------------------------------------------------------------------
    # Pipeline: extract
    # ------------------------------------------------------------------
    console.print("\n[cyan]── Extract[/cyan]")
    ext_table = Table(show_header=True)
    ext_table.add_column("Step", style="cyan")
    ext_table.add_column("Written", justify="right", style="green")
    ext_table.add_column("Skipped", justify="right", style="yellow")
    ext_table.add_column("Failed", justify="right", style="red")

    sig_result = run_rule_extraction(conn, run_date=target_date)
    ext_table.add_row("signals", str(sig_result.written), str(sig_result.skipped), str(sig_result.failed))

    ai_result = extract_action_items_from_all_notes(conn)
    ext_table.add_row("action items", str(ai_result.written), str(ai_result.skipped), str(ai_result.failed))

    dec_result = extract_decisions_from_all_notes(conn)
    ext_table.add_row("decisions", str(dec_result.written), str(dec_result.skipped), str(dec_result.failed))

    console.print(ext_table)

    # ------------------------------------------------------------------
    # Pipeline: brief
    # ------------------------------------------------------------------
    console.print("\n[cyan]── Brief[/cyan]")
    brief_obj = generate_daily_brief(conn, target_date=target_date)
    brief_file = write_brief_to_file(brief_obj, output_path=str(demo_brief_path))
    console.print(f"[green]Brief written:[/green] {brief_file}")

    # ------------------------------------------------------------------
    # Pipeline: closeout
    # ------------------------------------------------------------------
    console.print("\n[cyan]── Closeout[/cyan]")
    co_result = generate_closeout(conn, target_date=target_date)
    co_file = write_closeout_to_file(co_result, target_date, output_dir=str(demo_closeout_dir))
    console.print(f"[green]Closeout written:[/green] {co_file}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    console.print()
    console.print("[bold green]Demo reset complete.[/bold green]")
    console.print(f"  Database: {demo_db_path}")
    console.print(f"  Brief:    {brief_file}")
    console.print(f"  Closeout: {co_file}")
    if co_result.weekly_exec_content:
        weekly_path = demo_closeout_dir / f"{target_date.isoformat()}-weekly-exec.md"
        console.print(f"  Weekly:   {weekly_path}")


# ---------------------------------------------------------------------------
# manager-os profile-forecast
# ---------------------------------------------------------------------------


@app.command(name="profile-forecast")
def profile_forecast() -> None:
    """Profile the forecast data and report on its structure and contents."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection

    settings = get_settings()
    conn = get_connection(settings.db_path)

    console.print("[bold]Forecast Profile[/bold]")
    
    # Source mode
    src = getattr(settings, "forecast_source", "local_csv")
    console.print(f"  Source mode:      {src}")
    if src == "google_sheet_gemini":
        console.print(f"  Sheet ID:         {getattr(settings, 'forecast_sheet_id', 'N/A')}")
        console.print(f"  GID:              {getattr(settings, 'forecast_sheet_gid', 'N/A')}")
    console.print(f"  Local CSV path:   {getattr(settings, 'forecast_local_csv', '') or getattr(settings, 'forecast_csv', 'N/A')}")

    # Detect format
    try:
        from manager_os.ingest.forecast_wide import is_wide_format
        csv_path = getattr(settings, "forecast_local_csv", "") or getattr(settings, "forecast_csv", "")
        if csv_path and Path(csv_path).exists():
            is_wide = is_wide_format(csv_path)
            console.print(f"  Detected format:  {'wide sectioned forecast' if is_wide else 'normalized long format'}")
        else:
            console.print("  Detected format:  CSV not found at configured path")
    except Exception as e:
        console.print(f"  Detected format:  Error checking format: {e}")

    # Query DB for stats
    try:
        # Sections
        sections = conn.execute(
            "SELECT DISTINCT source_section FROM forecast_pipeline_demand WHERE source_section IS NOT NULL"
        ).fetchall()
        section_list = [r[0] for r in sections if r[0]]
        console.print(f"  Sections:         {', '.join(section_list) if section_list else 'None'}")

        # Week range
        week_range = conn.execute(
            "SELECT MIN(week_start), MAX(week_start) FROM forecast_pipeline_demand WHERE week_start IS NOT NULL"
        ).fetchone()
        if week_range and week_range[0] and week_range[1]:
            console.print(f"  Week range:       {week_range[0]} through {week_range[1]}")

        # Engineer count by section
        eng_counts = conn.execute(
            "SELECT source_section, COUNT(DISTINCT person_name) FROM staffing_forecast "
            "WHERE forecast_type = 'capacity' AND source_section IS NOT NULL GROUP BY source_section"
        ).fetchall()
        for sec, cnt in eng_counts:
            console.print(f"  Engineers ({sec}): {cnt}")

        # Pipeline scheduled demand count by section
        sched_counts = conn.execute(
            "SELECT source_section, COUNT(*) FROM forecast_pipeline_demand "
            "WHERE record_type = 'pipeline_demand' AND week_start IS NOT NULL GROUP BY source_section"
        ).fetchall()
        for sec, cnt in sched_counts:
            console.print(f"  Scheduled demand ({sec}): {cnt}")

        # Unscheduled pipeline opportunity count by section
        unsched_counts = conn.execute(
            "SELECT source_section, COUNT(*) FROM forecast_pipeline_demand "
            "WHERE record_type = 'pipeline_opportunity' GROUP BY source_section"
        ).fetchall()
        for sec, cnt in unsched_counts:
            console.print(f"  Unscheduled opportunities ({sec}): {cnt}")

        # Candidate engineer count
        cand_count = conn.execute(
            "SELECT COUNT(*) FROM forecast_pipeline_demand WHERE candidate_people IS NOT NULL AND candidate_people != '[]'"
        ).fetchone()[0]
        console.print(f"  Rows with candidates: {cand_count}")

        # Metric mismatch count
        mismatch_count = conn.execute(
            "SELECT COUNT(*) FROM forecast_summary_metric WHERE metric_value IS NOT NULL"  # Simplified, real mismatch logic is in parser
        ).fetchone()[0]
        # Actually, let's just count warnings if we stored them, but we don't store them in DB. 
        # We can mention that mismatches are logged during ingest.
        console.print("  Metric mismatches:  Logged during ingest (see warnings)")

        # Row counts by record type
        row_counts = conn.execute(
            "SELECT record_type, COUNT(*) FROM forecast_pipeline_demand GROUP BY record_type"
        ).fetchall()
        for rtype, cnt in row_counts:
            console.print(f"  {rtype} rows:       {cnt}")

    except Exception as e:
        console.print(f"  [yellow]Error querying DB: {e}[/yellow]")

    console.print()


# ---------------------------------------------------------------------------
# manager-os index-projects
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# manager-os project-index-fetch
# ---------------------------------------------------------------------------


@app.command(name="project-index-fetch")
def project_index_fetch(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print resolved config without downloading."),
    force: bool = typer.Option(False, "--force", help="Overwrite the local project index CSV."),
    print_url: bool = typer.Option(False, "--print-url", help="Print the deterministic CSV export URL and exit."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Print the Gemini CLI prompt and exit."),
    output: Optional[str] = typer.Option(None, "--output", help="Override the local CSV output path."),
    sheet_url: Optional[str] = typer.Option(None, "--sheet-url", help="Override the Google Sheet URL."),
    sheet_id: Optional[str] = typer.Option(None, "--sheet-id", help="Override the Google Sheet ID."),
    gid: Optional[str] = typer.Option(None, "--gid", help="Override the Google Sheet GID."),
    timeout: int = typer.Option(180, "--timeout", help="Download timeout in seconds."),
) -> None:
    """Fetch the project index CSV from the configured Google Sheet via Gemini CLI.

    Uses deterministic retrieval. No fallbacks allowed.
    """
    import subprocess
    import json
    import csv
    import hashlib
    from datetime import datetime
    from manager_os.config import get_settings

    settings = get_settings()

    # Resolve config
    src = getattr(settings, "project_index_source", "google_sheet_gemini")
    s_id = sheet_id or getattr(settings, "project_index_sheet_id", "")
    s_gid = gid or getattr(settings, "project_index_sheet_gid", "")
    s_url = sheet_url or getattr(settings, "project_index_sheet_url", "")
    local_csv = output or getattr(settings, "project_index_local_csv", "")

    if print_url:
        export_url = getattr(settings, "project_index_export_url", "")
        if not export_url and s_id and s_gid:
            export_url = f"https://docs.google.com/spreadsheets/d/{s_id}/export?format=csv&gid={s_gid}"
        console.print(f"[bold]Export URL:[/bold] {export_url}")
        raise typer.Exit(0)

    if dry_run:
        console.print("[bold]Project Index Fetch — Dry Run[/bold]")
        console.print(f"  Source:       {src}")
        console.print(f"  Sheet ID:     {s_id}")
        console.print(f"  GID:          {s_gid}")
        console.print(f"  Sheet URL:    {s_url}")
        console.print(f"  Output Path:  {local_csv}")
        raise typer.Exit(0)

    if src != "google_sheet_gemini":
        console.print(f"[red]MANAGER_OS_PROJECT_INDEX_SOURCE must be 'google_sheet_gemini'. Got '{src}'.[/red]")
        raise typer.Exit(1)

    if not s_id or not s_gid or not s_url:
        console.print("[red]Missing Sheet ID, GID, or URL. Check MANAGER_OS_PROJECT_INDEX_SHEET_ID, GID, and URL.[/red]")
        raise typer.Exit(1)

    if not local_csv:
        console.print("[red]No local CSV path configured (MANAGER_OS_PROJECT_INDEX_LOCAL_CSV).[/red]")
        raise typer.Exit(1)

    prompt = f"""You are operating in read-only mode.
Do not create, edit, delete, send, move, or modify anything.

Open this exact Google Sheet:
{s_url}

Read only the tab with gid:
{s_gid}

Return the raw tabular data from that tab only.

Do not summarize.
Do not infer.
Do not search Drive.
Do not choose a different spreadsheet.
Do not transform business semantics.
Do not omit blank cells.

Return strict JSON only with:
{{
  "ok": true,
  "source": "google_sheet_project_index",
  "source_url": "{s_url}",
  "sheet_id": "{s_id}",
  "gid": "{s_gid}",
  "retrieved_at": "...",
  "rows": [
    ["cell A1", "cell B1", "..."],
    ["cell A2", "cell B2", "..."]
  ]
}}

If you cannot access the sheet, return:
{{
  "ok": false,
  "source": "google_sheet_project_index",
  "sheet_id": "{s_id}",
  "gid": "{s_gid}",
  "error": "..."
}}"""

    if print_prompt:
        console.print("[bold]Gemini CLI Prompt:[/bold]")
        console.print(prompt)
        raise typer.Exit(0)

    console.print(f"[dim]Retrieving project index via Gemini CLI for Sheet ID: {s_id}, GID: {s_gid}[/dim]")
    console.print(f"[dim]Saving to:[/dim] {local_csv}")

    try:
        from manager_os.llm.gemini_cli import GEMINI_CLI_BIN, GEMINI_CLI_MODEL, GEMINI_CLI_ARGS, GEMINI_CLI_TIMEOUT
        
        cmd = [GEMINI_CLI_BIN]
        if GEMINI_CLI_MODEL:
            cmd.extend(["--model", GEMINI_CLI_MODEL])
        if GEMINI_CLI_ARGS:
            cmd.extend(GEMINI_CLI_ARGS.split())
        cmd.append("-y")
        
        effective_timeout = timeout if timeout else GEMINI_CLI_TIMEOUT
        
        proc = subprocess.run(
            cmd + ["--prompt", prompt],
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        
        if proc.returncode != 0:
            raise RuntimeError(f"Gemini CLI failed: {proc.stderr}")
            
        output_text = proc.stdout.strip()
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]
            
        result_data = json.loads(output_text)
        
        if not result_data.get("ok"):
            raise RuntimeError(f"Gemini CLI reported failure: {result_data.get('error', 'Unknown error')}")
            
        rows = result_data.get("rows", [])
        if not rows:
            raise RuntimeError("Gemini CLI returned no rows.")
            
        # Write to CSV
        Path(local_csv).parent.mkdir(parents=True, exist_ok=True)
        csv_content = ""
        with open(local_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
            f.seek(0)
            csv_content = f.read()
            
        content_hash = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()
        retrieved_at = result_data.get("retrieved_at", datetime.utcnow().isoformat())
        
        # Write metadata
        meta_path = f"{local_csv}.meta.json"
        metadata = {
            "source": "google_sheet_project_index",
            "sheet_url": s_url,
            "sheet_id": s_id,
            "gid": s_gid,
            "retrieved_at": retrieved_at,
            "local_csv_path": local_csv,
            "row_count": len(rows),
            "content_hash": content_hash
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
            
        console.print(f"[green]✓ Successfully retrieved and saved project index to {local_csv}[/green]")
        console.print(f"[dim]  Metadata written to: {meta_path}[/dim]")
        
    except subprocess.TimeoutExpired:
        console.print(f"[red]✗ Gemini CLI timed out after {timeout} seconds.[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Retrieval failed: {e}[/red]")
        console.print(f"  Sheet ID: {s_id}")
        console.print(f"  GID: {s_gid}")
        console.print(f"  Sheet URL: {s_url}")
        console.print("[yellow]Suggestion: Verify your Google account has access to this sheet and Gemini CLI is configured correctly.[/yellow]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# manager-os index-projects
# ---------------------------------------------------------------------------


@app.command(name="index-projects")
def index_projects(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing."),
    force: bool = typer.Option(False, "--force", help="Re-index all projects."),
    limit: int = typer.Option(None, "--limit", help="Limit number of projects to process."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    skip_fetch: bool = typer.Option(False, "--skip-fetch", help="Skip fetching the project sheet."),
    skip_drive_enrichment: bool = typer.Option(False, "--skip-drive-enrichment", help="Skip Google Drive document enrichment."),
    notes_enrichment: bool = typer.Option(False, "--notes-enrichment", help="Enable notes enrichment (not primary source)."),
) -> None:
    """Index projects from the NetSuite Closed-Won Opportunities sheet.

    Primary source is the Google Sheet. Notes are enrichment only.
    """
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.ingest.project_sheet import parse_project_sheet, upsert_projects
    import json
    import hashlib
    from datetime import datetime, timedelta

    settings = get_settings()
    conn = get_connection(settings.db_path)

    if dry_run:
        console.print("[bold]Index Projects — Dry Run[/bold]")
        console.print(f"  Force:                {force}")
        console.print(f"  Limit:                {limit}")
        console.print(f"  Skip Fetch:           {skip_fetch}")
        console.print(f"  Skip Drive Enrichment: {skip_drive_enrichment}")
        console.print(f"  Notes Enrichment:     {notes_enrichment}")
        raise typer.Exit(0)

    # Step 1: Fetch project sheet if not skipped
    local_csv = getattr(settings, "project_index_local_csv", "")
    if not local_csv:
        console.print("[red]No local CSV path configured (MANAGER_OS_PROJECT_INDEX_LOCAL_CSV).[/red]")
        raise typer.Exit(1)

    if not skip_fetch:
        console.print("[bold]Step 1: Fetching project sheet...[/bold]")
        # Call project-index-fetch logic inline
        # For now, we'll just check if the file exists and is fresh
        meta_path = f"{local_csv}.meta.json"
        if not Path(meta_path).exists():
            console.print("[red]Project index metadata not found. Run 'manager-os project-index-fetch' first.[/red]")
            raise typer.Exit(1)
        
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        
        # Verify freshness
        stale_hours = getattr(settings, "project_index_stale_after_hours", 24)
        retrieved_at_str = meta.get("retrieved_at", "")
        if retrieved_at_str:
            retrieved_at_str = retrieved_at_str.replace("Z", "+00:00")
            try:
                retrieved_at = datetime.fromisoformat(retrieved_at_str)
                if retrieved_at.tzinfo is None:
                    retrieved_at = retrieved_at.replace(tzinfo=datetime.now().astimezone().tzinfo)
                
                now = datetime.now(retrieved_at.tzinfo)
                if (now - retrieved_at) > timedelta(hours=stale_hours):
                    console.print(f"[red]Project index is stale (retrieved {retrieved_at.isoformat()}, stale after {stale_hours}h).[/red]")
                    console.print("[red]Run 'manager-os project-index-fetch --force' to refresh.[/red]")
                    raise typer.Exit(1)
            except ValueError:
                console.print(f"[red]Invalid retrieved_at format in metadata: {retrieved_at_str}[/red]")
                raise typer.Exit(1)
    else:
        console.print("[bold]Step 1: Skipping fetch (--skip-fetch)[/bold]")

    # Step 2: Parse and upsert projects
    console.print("[bold]Step 2: Parsing project sheet...[/bold]")
    parse_result = parse_project_sheet(local_csv)
    
    if parse_result.errors:
        for error in parse_result.errors:
            console.print(f"[red]✗ {error}[/red]")
        raise typer.Exit(1)
    
    if parse_result.warnings and verbose:
        for warning in parse_result.warnings:
            console.print(f"[yellow]⚠ {warning}[/yellow]")
    
    console.print(f"  Parsed {len(parse_result.projects)} projects")
    console.print(f"  Skipped {parse_result.skipped_rows} rows")
    
    if limit:
        parse_result.projects = parse_result.projects[:limit]
    
    inserted, updated = upsert_projects(conn, parse_result.projects, force=force)
    console.print(f"[green]✓ Inserted {inserted} projects, updated {updated} projects[/green]")

    # Step 3: Drive enrichment (if enabled)
    if not skip_drive_enrichment and getattr(settings, "project_doc_search_enabled", True):
        console.print("[bold]Step 3: Enriching with Google Drive documents...[/bold]")
        from manager_os.ingest.project_drive_docs import search_drive_for_project_docs, upsert_project_documents
        
        doc_limit = getattr(settings, "project_doc_search_limit_per_project", 10)
        total_docs = 0
        
        for project in parse_result.projects:
            if not project.opportunity_number:
                continue
            
            project_id = f"project::{project.opportunity_number}"
            console.print(f"  Searching Drive for {project.opportunity_number}...")
            
            drive_result = search_drive_for_project_docs(
                opportunity_number=project.opportunity_number,
                client=project.client,
                project_name=project.project_name,
                timeout=120,
            )
            
            if drive_result.errors and verbose:
                for error in drive_result.errors:
                    console.print(f"    [red]✗ {error}[/red]")
            
            # Set project_id on documents
            for doc in drive_result.documents:
                doc.project_id = project_id
            
            if drive_result.documents:
                inserted_docs, updated_docs = upsert_project_documents(conn, drive_result.documents, force=force)
                total_docs += len(drive_result.documents)
                if verbose:
                    console.print(f"    Found {len(drive_result.documents)} documents")
        
        console.print(f"[green]✓ Enriched with {total_docs} documents[/green]")
    else:
        console.print("[bold]Step 3: Skipping Drive enrichment[/bold]")

    # Step 4: Notes enrichment (if enabled)
    if notes_enrichment:
        console.print("[bold]Step 4: Enriching with notes...[/bold]")
        from manager_os.build.project_index import extract_projects_from_notes
        notes_count = extract_projects_from_notes(conn, force=force, limit=limit)
        console.print(f"[green]✓ Enriched with {notes_count} notes[/green]")
    else:
        console.print("[bold]Step 4: Skipping notes enrichment[/bold]")

    console.print("[green]✓ Project indexing complete[/green]")


# ---------------------------------------------------------------------------
# manager-os search-projects
# ---------------------------------------------------------------------------


@app.command(name="search-projects")
def search_projects(
    query: str = typer.Argument("", help="Search query."),
    client: str = typer.Option("", "--client", help="Filter by client."),
    person: str = typer.Option("", "--person", help="Filter by person (team member)."),
    technology: str = typer.Option("", "--technology", help="Filter by technology."),
    project_type: str = typer.Option("", "--type", help="Filter by project type (ADK, GenAI, CES, etc.)."),
    industry: str = typer.Option("", "--industry", help="Filter by industry."),
    sales_rep: str = typer.Option("", "--sales-rep", help="Filter by sales rep."),
    status: str = typer.Option("", "--status", help="Filter by status."),
    year: int = typer.Option(None, "--year", help="Filter by year."),
    close_after: str = typer.Option("", "--close-after", help="Filter by close date (YYYY-MM-DD)."),
    close_before: str = typer.Option("", "--close-before", help="Filter by close date (YYYY-MM-DD)."),
    opportunity_number: str = typer.Option("", "--opportunity-number", help="Filter by exact opportunity number."),
    document_type: str = typer.Option("", "--document-type", help="Filter by related document type."),
    limit: int = typer.Option(20, "--limit", help="Max results."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Search the project knowledge index (NetSuite Closed-Won Opportunities)."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.project_index import search_projects

    settings = get_settings()
    conn = get_connection(settings.db_path)

    results = search_projects(
        conn,
        query=query,
        client=client,
        person=person,
        technology=technology,
        project_type=project_type,
        industry=industry,
        sales_rep=sales_rep,
        status=status,
        year=year,
        close_after=close_after,
        close_before=close_before,
        opportunity_number=opportunity_number,
        document_type=document_type,
        limit=limit,
    )

    if as_json:
        import json
        console.print(json.dumps(results, indent=2))
    else:
        if not results:
            console.print("[yellow]No projects found matching criteria.[/yellow]")
        else:
            for r in results:
                console.print(f"\n[bold]{r['project_name']}[/bold] ({r['client']})")
                console.print(f"  OppID: {r['opportunity_number']} | Status: {r['status']}")
                if r.get('close_date'):
                    console.print(f"  Close Date: {r['close_date']}")
                if r.get('services_amount'):
                    console.print(f"  Services Amount: ${r['services_amount']:,.2f}")
                if r.get('project_type'):
                    console.print(f"  Type: {r['project_type']}")
                if r.get('industry'):
                    console.print(f"  Industry: {r['industry']}")
                if r.get('sales_rep'):
                    console.print(f"  Sales Rep: {r['sales_rep']}")
                if r.get('technologies'):
                    console.print(f"  Technologies: {', '.join(r['technologies'])}")
                if r.get('short_description'):
                    console.print(f"  Short: {r['short_description']}")
                if r.get('summary'):
                    console.print(f"  Summary: {r['summary'][:150]}...")
                if r.get('related_documents'):
                    console.print(f"  Documents: {len(r['related_documents'])} found")
                    for doc in r['related_documents'][:3]:
                        console.print(f"    - {doc['document_type']}: {doc['title']}")


# ---------------------------------------------------------------------------
# manager-os match-projects
# ---------------------------------------------------------------------------


@app.command(name="match-projects")
def match_projects(
    deal_id: str = typer.Option("", "--deal-id", help="Deal ID to match."),
    opportunity_number: str = typer.Option("", "--opportunity-number", help="Opportunity number to match."),
    limit: int = typer.Option(5, "--limit", help="Max matches to return."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Find similar past projects for a given deal to accelerate delivery intelligence."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.similar_projects import find_similar_projects

    settings = get_settings()
    conn = get_connection(settings.db_path)

    if dry_run:
        console.print("[bold]Match Projects — Dry Run[/bold]")
        console.print(f"  Deal ID:          {deal_id}")
        console.print(f"  Opportunity #:    {opportunity_number}")
        console.print(f"  Limit:            {limit}")
        raise typer.Exit(0)

    if not deal_id and not opportunity_number:
        console.print("[yellow]Please provide either --deal-id or --opportunity-number.[/yellow]")
        raise typer.Exit(1)

    matches = find_similar_projects(
        conn,
        deal_id=deal_id,
        opportunity_number=opportunity_number,
        limit=limit,
    )

    if as_json:
        import json
        console.print(json.dumps(matches, indent=2))
    else:
        if not matches:
            console.print("[yellow]No similar past projects found.[/yellow]")
        else:
            console.print(f"[bold]Found {len(matches)} similar project(s):[/bold]")
            for m in matches:
                console.print(f"\n  [bold]{m['project_name']}[/bold] ({m['client']}) - Score: {m['score']}")
                console.print(f"  Why: {m['why_it_matched']}")
                if m['lessons_learned']:
                    console.print(f"  Lessons: {m['lessons_learned'][:100]}...")


# ---------------------------------------------------------------------------
# manager-os status
# ---------------------------------------------------------------------------

# GWS source_type values stored in raw_documents
_GWS_SOURCE_TYPES = {"gws", "gws:calendar", "gws:gmail", "gws:chat", "gmail"}

# source_type → display label for the "by source" table
_SOURCE_DISPLAY: dict[str, str] = {
    "obsidian": "obsidian",
    "workspace_summary": "summary",
    "gws": "gws",
    "gws:calendar": "gws_calendar",
    "gws:gmail": "gws_gmail",
    "gws:chat": "gws_chat",
    "gmail": "gws_gmail",
}

# Severity ordering for the open-signals section
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _detect_mode(db_path: str, vault_path: str) -> str:
    """Return 'demo', 'sample data', or 'production'."""
    db_lower = db_path.lower()
    if "demo" in db_lower:
        return "demo"
    if vault_path:
        vp = Path(vault_path)
        if _within_repo(vp) or _path_has_safe_keyword(vp):
            return "sample data"
    return "production"


def _db_source_path_counts(conn) -> tuple[int, int]:
    """Return (fixture_count, real_count) of source_paths in raw_documents.

    A path is considered a fixture/demo path when it contains a fixture/demo
    keyword OR is inside the project repo root. A path is considered real when
    it is NOT a fixture path and NOT a special in-memory marker like ':memory:'.
    """
    rows = conn.execute("SELECT source_path FROM raw_documents").fetchall()
    if not rows:
        return 0, 0

    fixture_count = 0
    real_count = 0
    for (sp,) in rows:
        if not sp:
            continue
        p = Path(sp)
        if _within_repo(p) or _path_has_safe_keyword(p):
            fixture_count += 1
        else:
            real_count += 1
    return fixture_count, real_count


def _is_sample_config(settings, conn=None) -> bool:
    """Return True when the active DB contains only fixture/demo data.

    Checks actual source_paths stored in raw_documents when a connection is
    available — this avoids false positives when the DB file lives inside the
    repo but contains real Obsidian vault records.

    Falls back to config-path heuristics when the DB is empty or no connection
    is given.
    """
    # If we have a DB connection, inspect actual ingested paths
    if conn is not None:
        fixture_count, real_count = _db_source_path_counts(conn)
        if real_count > 0:
            return False   # at least one real path → not sample-only
        if fixture_count > 0:
            return True    # only fixture paths → sample data

    # No rows or no connection — fall back to config-path heuristics
    if settings.vault_path:
        vp = Path(settings.vault_path)
        if _within_repo(vp) or _path_has_safe_keyword(vp):
            return True
    db_p = Path(settings.db_path)
    if _path_has_safe_keyword(db_p):
        return True
    return False


@app.command()
def status() -> None:
    """Show a summary of database contents and configuration.

    Displays table counts, document sources, open signals by severity,
    open action items, and a warning when sample/demo data is detected.
    Useful for inspecting the database before and after ingesting real data.
    """
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from rich.panel import Panel
    from rich import box as rich_box

    settings = get_settings()
    db_path_str = settings.db_path

    # Open DB (creates tables if needed but never modifies data)
    conn = get_connection(db_path_str)

    mode = _detect_mode(db_path_str, settings.vault_path)
    sample_warning = _is_sample_config(settings, conn=conn)

    # ── header ──────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        f"[bold]Manager OS — Database Status[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))
    console.print(f"  [dim]Database:[/dim]  {db_path_str}")
    mode_color = {"demo": "yellow", "sample data": "yellow", "production": "green"}.get(mode, "white")
    console.print(f"  [dim]Mode:[/dim]      [{mode_color}]{mode}[/{mode_color}]")

    # ── latest dates ────────────────────────────────────────────────────────
    latest_note = conn.execute("SELECT MAX(note_date) FROM notes").fetchone()[0]
    latest_brief = conn.execute("SELECT MAX(brief_date) FROM daily_briefs").fetchone()[0]

    console.print()
    dates_table = Table(show_header=False, box=None, pad_edge=False, show_edge=False)
    dates_table.add_column("Label", style="dim", no_wrap=True)
    dates_table.add_column("Value")
    dates_table.add_row("Latest note date:", str(latest_note) if latest_note else "[dim]none[/dim]")
    dates_table.add_row("Latest brief date:", str(latest_brief) if latest_brief else "[dim]none[/dim]")
    console.print(dates_table)

    # ── table counts ────────────────────────────────────────────────────────
    _COUNTED_TABLES = [
        "people",
        "clients",
        "raw_documents",
        "notes",
        "deals",
        "staffing_forecast",
        "meetings",
        "signals",
        "action_items",
        "decisions",
        "daily_briefs",
        "meeting_prep",
    ]

    console.print()
    counts_table = Table(
        title="Table Counts",
        show_header=True,
        header_style="bold",
        box=rich_box.SIMPLE,
    )
    counts_table.add_column("Table", style="cyan", no_wrap=True)
    counts_table.add_column("Rows", justify="right")

    for tbl in _COUNTED_TABLES:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception:
            n = 0
        style = "green" if n > 0 else "dim"
        counts_table.add_row(tbl, f"[{style}]{n}[/{style}]")
    console.print(counts_table)

    # ── documents by source ─────────────────────────────────────────────────
    src_rows = conn.execute(
        "SELECT source_type, COUNT(*) as cnt FROM raw_documents GROUP BY source_type ORDER BY source_type"
    ).fetchall()

    # Aggregate into canonical display labels
    agg: dict[str, int] = {}
    for src_type, cnt in src_rows:
        label = _SOURCE_DISPLAY.get(src_type, src_type)
        agg[label] = agg.get(label, 0) + cnt

    # Also include meetings table gws counts if raw_documents is empty for gws
    if not any(k.startswith("gws") for k in agg):
        gws_mtg = conn.execute(
            "SELECT COUNT(*) FROM meetings WHERE source LIKE 'gws%'"
        ).fetchone()[0]
        if gws_mtg:
            agg["gws_calendar (meetings)"] = gws_mtg

    if agg:
        console.print()
        src_table = Table(
            title="Documents by Source",
            show_header=True,
            header_style="bold",
            box=rich_box.SIMPLE,
        )
        src_table.add_column("Source", style="cyan", no_wrap=True)
        src_table.add_column("Count", justify="right")
        for label, cnt in sorted(agg.items()):
            style = "green" if cnt > 0 else "dim"
            src_table.add_row(label, f"[{style}]{cnt}[/{style}]")
        console.print(src_table)

    # ── open signals by severity ─────────────────────────────────────────────
    sig_rows = conn.execute(
        """
        SELECT severity, COUNT(*) as cnt
        FROM signals
        WHERE status = 'open'
        GROUP BY severity
        """
    ).fetchall()

    if sig_rows:
        console.print()
        sig_table = Table(
            title="Open Signals by Severity",
            show_header=True,
            header_style="bold",
            box=rich_box.SIMPLE,
        )
        sig_table.add_column("Severity", style="cyan", no_wrap=True)
        sig_table.add_column("Count", justify="right")
        _SEV_STYLE = {"critical": "red bold", "high": "red", "medium": "yellow", "low": "dim"}
        sorted_sigs = sorted(sig_rows, key=lambda r: _SEVERITY_ORDER.get(r[0], 99))
        for sev, cnt in sorted_sigs:
            s = _SEV_STYLE.get(sev, "white")
            sig_table.add_row(f"[{s}]{sev}[/{s}]", str(cnt))
        console.print(sig_table)

    # ── open action items ───────────────────────────────────────────────────
    open_ai = conn.execute(
        "SELECT COUNT(*) FROM action_items WHERE status = 'open'"
    ).fetchone()[0]
    console.print()
    ai_style = "yellow" if open_ai > 0 else "dim"
    console.print(f"  [dim]Open action items:[/dim]  [{ai_style}]{open_ai}[/{ai_style}]")

    # ── sample/demo warning ──────────────────────────────────────────────────
    if sample_warning:
        fixture_count, real_count = _db_source_path_counts(conn)
        console.print()
        if real_count > 0:
            # Mixed DB — show counts and be precise
            console.print(
                f"[yellow]⚠  Mixed data detected:[/yellow] "
                f"{fixture_count} fixture/demo path(s) and {real_count} real path(s) in the DB."
            )
        else:
            console.print(
                "[yellow]⚠  Sample data detected:[/yellow] source paths point to "
                "fixture/demo data, not a real Obsidian vault."
            )
            console.print(
                "   Run [bold]manager-os demo-reset[/bold] to rebuild the demo database, "
                "or update your [bold].env[/bold] to point at real data."
            )
    console.print()


# ---------------------------------------------------------------------------
# Readiness check helpers
# ---------------------------------------------------------------------------


class _Check:
    """A single PASS / WARN / FAIL result for the readiness command."""

    __slots__ = ("label", "status", "note")

    def __init__(self, label: str, status: str, note: str = "") -> None:
        self.label = label
        self.status = status  # "PASS" | "WARN" | "FAIL"
        self.note = note


# Canonical sample/fixture person names shipped with the repo.
# If any of these appear in the live config, warn the user to replace them.
_SAMPLE_PERSON_NAMES: frozenset[str] = frozenset({
    "alice chen",
    "bob martinez",
    "carmen liu",
    "david park",
    "elena torres",
})

# Canonical sample/fixture client names shipped with the repo.
_SAMPLE_CLIENT_NAMES: frozenset[str] = frozenset({
    "acme corp",
    "big retail co",
    "finserv partners",
    "medtech solutions",
    "global logistics inc",
})

# (display_label, exact gitignore pattern that must be present)
_REQUIRED_GITIGNORE_RULES: list[tuple[str, str]] = [
    (".env", ".env"),
    ("data/raw/", "data/raw/"),
    ("data/processed/", "data/processed/"),
    ("output/", "output/"),
    ("*.duckdb", "*.duckdb"),
    ("*.duckdb.wal", "*.duckdb.wal"),
]


def _gitignore_lines(gitignore_path: Path) -> frozenset[str]:
    """Return active (non-comment, non-blank) lines from a .gitignore file."""
    if not gitignore_path.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in gitignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _check_gitignore(gitignore_path: Path) -> list[_Check]:
    """Return one _Check per required gitignore pattern."""
    if not gitignore_path.exists():
        return [
            _Check(f".gitignore: {label}", "FAIL", ".gitignore not found")
            for label, _ in _REQUIRED_GITIGNORE_RULES
        ]
    lines = _gitignore_lines(gitignore_path)
    results: list[_Check] = []
    for label, pattern in _REQUIRED_GITIGNORE_RULES:
        covered = (
            pattern in lines
            # Accept "data/raw/*" or "data/raw/" for the "data/raw/" check
            or (pattern.endswith("/") and (pattern + "*") in lines)
            or (pattern.endswith("/") and (pattern.rstrip("/") + "/*") in lines)
        )
        results.append(_Check(
            f".gitignore: {label}",
            "PASS" if covered else "FAIL",
            "" if covered else f"Pattern '{pattern}' not found in .gitignore",
        ))
    return results


# ---------------------------------------------------------------------------
# manager-os readiness
# ---------------------------------------------------------------------------


@app.command()
def readiness() -> None:
    """Check whether Manager OS is safe and ready for real local data sources.

    Inspects environment variables, source paths, config files, and .gitignore
    rules. Prints a PASS / WARN / FAIL row for each check.

    Exits 0 when no blocking failures are found (warnings are allowed).
    Exits 1 when one or more checks are FAIL.

    Run this before your first real-data ingest.
    """
    import os

    from manager_os.config import get_settings, load_clients, load_people
    from rich import box as rich_box
    from rich.panel import Panel

    settings = get_settings()
    checks: list[_Check] = []

    # ── 1. .env file or env vars present ─────────────────────────────────────
    env_file = _REPO_ROOT / ".env"
    _required_env_vars = [
        "MANAGER_OS_VAULT_PATH",
        "MANAGER_OS_DB_PATH",
        "MANAGER_OS_FORECAST_CSV",
        "MANAGER_OS_DEALS_CSV",
    ]
    if env_file.exists():
        checks.append(_Check(".env file", "PASS"))
    else:
        missing_vars = [v for v in _required_env_vars if not os.environ.get(v)]
        if missing_vars:
            checks.append(_Check(
                ".env file",
                "FAIL",
                f"No .env and missing vars: {', '.join(missing_vars)}",
            ))
        else:
            checks.append(_Check(
                ".env file",
                "WARN",
                "No .env — required vars found in environment",
            ))

    # ── 2. Vault path ─────────────────────────────────────────────────────────
    vault = settings.vault_path
    if not vault:
        checks.append(_Check("MANAGER_OS_VAULT_PATH", "FAIL", "Not set"))
    else:
        vp = Path(vault)
        checks.append(_Check("MANAGER_OS_VAULT_PATH", "PASS"))
        if not vp.exists():
            checks.append(_Check("vault directory exists", "FAIL", f"Not found: {vault}"))
        else:
            checks.append(_Check("vault directory exists", "PASS"))
            if _within_repo(vp):
                checks.append(_Check(
                    "vault not in repo",
                    "WARN",
                    "Vault is inside the repo — notes may be accidentally staged",
                ))
            vault_lower = vault.lower()
            if "googledrive" in vault_lower or "google drive" in vault_lower:
                checks.append(_Check(
                    "vault sync",
                    "WARN",
                    "Path appears Google Drive synced — sync conflicts possible; safe to continue",
                ))

    # ── 3. Forecast CSV ───────────────────────────────────────────────────────
    fcsv = settings.forecast_csv
    if not fcsv:
        checks.append(_Check("MANAGER_OS_FORECAST_CSV", "FAIL", "Not set"))
    else:
        fp = Path(fcsv)
        if not fp.exists():
            checks.append(_Check("MANAGER_OS_FORECAST_CSV", "FAIL", f"File not found: {fcsv}"))
        else:
            checks.append(_Check("MANAGER_OS_FORECAST_CSV", "PASS"))

    # ── 4. Deals CSV ──────────────────────────────────────────────────────────
    dcsv = settings.deals_csv
    if not dcsv:
        checks.append(_Check("MANAGER_OS_DEALS_CSV", "FAIL", "Not set"))
    else:
        dp = Path(dcsv)
        if not dp.exists():
            checks.append(_Check("MANAGER_OS_DEALS_CSV", "FAIL", f"File not found: {dcsv}"))
        else:
            checks.append(_Check("MANAGER_OS_DEALS_CSV", "PASS"))

    # ── 5. DB path is in a gitignored location ────────────────────────────────
    db_str = settings.db_path
    db_p = Path(db_str)
    db_norm = db_str.replace("\\", "/")
    db_gitignored = (
        db_str.endswith(".duckdb")           # covered by *.duckdb rule
        or not _within_repo(db_p)            # outside repo entirely
        or "data/processed" in db_norm       # gitignored dir
        or "data/demo" in db_norm            # gitignored dir
    )
    checks.append(_Check(
        "DB path gitignored",
        "PASS" if db_gitignored else "WARN",
        "" if db_gitignored else (
            f"'{db_str}' inside repo without .duckdb extension — verify .gitignore covers it"
        ),
    ))

    # ── 6. config/people.yaml ─────────────────────────────────────────────────
    try:
        people = load_people(settings)
        names_lower = {p.name.lower() for p in people}
        overlap = names_lower & _SAMPLE_PERSON_NAMES
        if overlap:
            checks.append(_Check(
                "config/people.yaml",
                "WARN",
                f"Contains sample names: {', '.join(sorted(overlap))}",
            ))
        else:
            checks.append(_Check("config/people.yaml", "PASS", f"{len(people)} person(s)"))
    except FileNotFoundError:
        checks.append(_Check("config/people.yaml", "FAIL", "File not found"))
    except Exception as exc:
        checks.append(_Check("config/people.yaml", "FAIL", str(exc)))

    # ── 7. config/clients.yaml ────────────────────────────────────────────────
    try:
        clients = load_clients(settings)
        names_lower = {c.name.lower() for c in clients}
        overlap = names_lower & _SAMPLE_CLIENT_NAMES
        if overlap:
            checks.append(_Check(
                "config/clients.yaml",
                "WARN",
                f"Contains sample names: {', '.join(sorted(overlap))}",
            ))
        else:
            checks.append(_Check("config/clients.yaml", "PASS", f"{len(clients)} client(s)"))
    except FileNotFoundError:
        checks.append(_Check("config/clients.yaml", "FAIL", "File not found"))
    except Exception as exc:
        checks.append(_Check("config/clients.yaml", "FAIL", str(exc)))

    # ── 8. config/deal_aliases.yaml ───────────────────────────────────────────
    config_dir_p = Path(settings.config_dir)
    aliases_path = config_dir_p / "deal_aliases.yaml"
    if aliases_path.exists():
        checks.append(_Check("config/deal_aliases.yaml", "PASS"))
    else:
        checks.append(_Check("config/deal_aliases.yaml", "FAIL", f"Not found: {aliases_path}"))

    # ── 9. .gitignore rules ───────────────────────────────────────────────────
    checks.extend(_check_gitignore(_REPO_ROOT / ".gitignore"))

    # ── Render results table ──────────────────────────────────────────────────
    _STATUS_STYLE = {"PASS": "green", "WARN": "yellow", "FAIL": "red bold"}
    _STATUS_ICON = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}

    console.print()
    console.print(Panel.fit(
        "[bold]Manager OS — Readiness Check[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))
    console.print()

    tbl = Table(
        show_header=True,
        header_style="bold",
        box=rich_box.SIMPLE,
        show_edge=False,
        pad_edge=False,
    )
    tbl.add_column("Check", style="dim", no_wrap=True, min_width=35)
    tbl.add_column("Status", no_wrap=True, min_width=8)
    tbl.add_column("Notes")

    for chk in checks:
        style = _STATUS_STYLE.get(chk.status, "white")
        icon = _STATUS_ICON.get(chk.status, "?")
        tbl.add_row(
            chk.label,
            f"[{style}]{icon} {chk.status}[/{style}]",
            f"[dim]{chk.note}[/dim]" if chk.note else "",
        )
    console.print(tbl)
    console.print()

    fails = [c for c in checks if c.status == "FAIL"]
    warns = [c for c in checks if c.status == "WARN"]

    if fails:
        console.print(
            f"[red bold]✗  {len(fails)} blocking failure(s).[/red bold] "
            "Fix the items above before running ingest."
        )
        raise typer.Exit(1)
    elif warns:
        console.print(
            f"[yellow]⚠  {len(warns)} warning(s) — review before connecting real data.[/yellow]\n"
            "[green]   No blocking failures. Safe to proceed.[/green]"
        )
    else:
        console.print(
            "[green bold]✓  All checks passed. Safe to run manager-os ingest.[/green bold]"
        )
    console.print()


# ---------------------------------------------------------------------------
# manager-os profile-forecast
# ---------------------------------------------------------------------------

# Maps canonical internal field name → display label used in the output table.
_FORECAST_FIELD_DISPLAY: dict[str, str] = {
    "person_name": "person",
    "week_start": "start_date",
    "client": "client",
    "project": "engagement",
    "allocation_pct": "allocation",
    "forecast_type": "status",
    "notes": "notes",
}

# Issue type → short display label for the issues table.
_ISSUE_LABEL: dict[str, str] = {
    "overallocated": "overallocated",
    "zero_allocation": "zero allocation",
    "missing_allocation": "missing allocation",
    "malformed_allocation": "malformed allocation",
    "missing_date": "missing date",
    "malformed_date": "malformed date",
    "unknown_person": "unknown person",
    "unknown_client": "unknown client",
    "metric_mismatch": "metric mismatch",
}


@app.command(name="profile-forecast")
def profile_forecast(
    path: Optional[str] = typer.Option(
        None,
        "--path",
        help="Path to forecast CSV. Defaults to MANAGER_OS_FORECAST_CSV.",
    ),
    sample_size: int = typer.Option(
        10,
        "--sample-size",
        help="Number of sample rows to display.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as JSON.",
    ),
) -> None:
    """Validate the configured forecast CSV before ingesting real data.

    Reads headers and a sample of rows without writing to the database.
    Shows detected columns, field mapping, allocation sanity checks, and
    any unknown person or client names compared to config/people.yaml and
    config/clients.yaml.

    Exits 0 when profiling completes (even with warnings).
    Exits 1 only if the file cannot be read or required columns are missing.
    """
    import json as _json

    from manager_os.config import get_settings, load_clients, load_people, load_source_priority
    from manager_os.profile import ForecastProfile, RowIssue, profile_forecast_csv, _FIELD_DISPLAY
    from rich import box as rich_box
    from rich.panel import Panel

    settings = get_settings()
    csv_path = path or settings.forecast_csv

    if not csv_path:
        console.print("[red]No forecast CSV path set. Use --path or set MANAGER_OS_FORECAST_CSV.[/red]")
        raise typer.Exit(1)

    # Load config (best-effort — missing config is non-fatal)
    try:
        people = load_people(settings)
    except Exception:
        people = None

    try:
        clients = load_clients(settings)
    except Exception:
        clients = None

    try:
        sp = load_source_priority(settings)
    except Exception:
        sp = None

    # Run the profiler
    try:
        result: ForecastProfile = profile_forecast_csv(
            csv_path,
            people=people,
            clients=clients,
            source_priority=sp,
            sample_size=sample_size,
        )
    except RuntimeError as exc:
        if json_output:
            console.print_json(_json.dumps({"error": str(exc), "can_ingest": False}))
        else:
            console.print(f"[red]✗  {exc}[/red]")
        raise typer.Exit(1)

    # ── JSON output ──────────────────────────────────────────────────────────
    if json_output:
        console.print_json(_json.dumps(result.to_dict()))
        if not result.can_ingest:
            raise typer.Exit(1)
        return

    # ── Human-readable output ────────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        "[bold]Manager OS — Forecast CSV Profile[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))
    console.print(f"  [dim]File:[/dim]    {result.path}")
    console.print(f"  [dim]Rows:[/dim]    {result.total_rows}")
    console.print(f"  [dim]Format:[/dim]  {result.detected_format}")
    console.print(f"  [dim]Sample:[/dim]  {result.sample_size} of {result.total_rows}")
    console.print()

    # ── Wide-format summary block ────────────────────────────────────────────
    if result.detected_format == "wide" and result.wide_summary:
        ws = result.wide_summary
        wide_tbl = Table(
            title="Wide Forecast Summary",
            show_header=False,
            box=None,
            pad_edge=False,
            show_edge=False,
        )
        wide_tbl.add_column("Label", style="dim", no_wrap=True)
        wide_tbl.add_column("Value")

        sections_str = ", ".join(ws.get("sections", []))
        wide_tbl.add_row("Sections", sections_str or "[dim]none detected[/dim]")
        wide_tbl.add_row("Person forecast rows", str(ws.get("person_forecast_rows", 0)))
        wide_tbl.add_row("Pipeline demand rows", str(ws.get("pipeline_demand_rows", 0)))
        wide_tbl.add_row("Pipeline opportunities", str(ws.get("pipeline_opportunity_rows", 0)))
        wide_tbl.add_row("Summary metric rows", str(ws.get("summary_metric_rows", 0)))
        wide_tbl.add_row("Candidate people", str(ws.get("candidate_people_total", 0)))
        wide_tbl.add_row("Unassigned demand rows", str(ws.get("unassigned_pipeline_demand", 0)))
        mismatch_count = ws.get("metric_mismatches", 0)
        mismatch_display = (
            f"[red]{mismatch_count}[/red]" if mismatch_count else "[green]0[/green]"
        )
        wide_tbl.add_row("Metric mismatches", mismatch_display)
        hire_weeks = ws.get("hire_status_weeks", [])
        if hire_weeks:
            hire_display = ", ".join(
                w for w in hire_weeks if str(w).upper().startswith("HIRE")
            ) or ", ".join(hire_weeks[:3])
            wide_tbl.add_row("HIRE weeks", hire_display or "[dim]none[/dim]")
        console.print(wide_tbl)
        console.print()
        console.print(
            "[dim]ℹ  Pipeline prospect/deal labels are NOT validated against "
            "config/clients.yaml — they are prospects, not signed clients.[/dim]"
        )
        console.print(
            "[dim]ℹ  Candidate Engineer(s) are possible staffing candidates only — "
            "NOT allocated, soft-held, or committed.[/dim]"
        )
        console.print()

    else:
        # Column mapping table
        col_tbl = Table(
            title="Column Mapping",
            show_header=True,
            header_style="bold",
            box=rich_box.SIMPLE,
            show_edge=False,
            pad_edge=False,
        )
        col_tbl.add_column("Raw Column", style="dim", no_wrap=True)
        col_tbl.add_column("Normalised", no_wrap=True)
        col_tbl.add_column("Field", style="cyan", no_wrap=True)

        for raw, norm in result.column_mapping.items():
            display = _FIELD_DISPLAY.get(norm, "")
            changed = raw != norm
            norm_display = f"[green]{norm}[/green]" if changed else f"[dim]{norm}[/dim]"
            col_tbl.add_row(raw, norm_display, display)
        console.print(col_tbl)
        console.print()

        # Required fields status
        req_tbl = Table(
            title="Required Fields",
            show_header=False,
            box=None,
            pad_edge=False,
            show_edge=False,
        )
        req_tbl.add_column("Status", no_wrap=True)
        req_tbl.add_column("Field")

        _REQ = ["person_name", "week_start"]
        for canonical in _REQ:
            display = _FIELD_DISPLAY.get(canonical, canonical)
            if canonical in result.fields_found:
                req_tbl.add_row("[green]✓ FOUND[/green]", f"{display} ({canonical})")
            else:
                req_tbl.add_row("[red]✗ MISSING[/red]", f"[red]{display} ({canonical})[/red]")
        console.print(req_tbl)
        console.print()

        # Optional fields coverage
        opt_tbl = Table(
            title="Optional Fields",
            show_header=False,
            box=None,
            pad_edge=False,
            show_edge=False,
        )
        opt_tbl.add_column("Status", no_wrap=True)
        opt_tbl.add_column("Field")

        _OPT_DISPLAY = [
            ("client", "client"),
            ("project", "engagement"),
            ("allocation_pct", "allocation"),
            ("forecast_type", "status"),
        ]
        for canonical, display in _OPT_DISPLAY:
            if canonical in result.fields_found:
                opt_tbl.add_row("[green]✓[/green]", f"{display} ({canonical})")
            else:
                opt_tbl.add_row("[dim]–[/dim]", f"[dim]{display} (not in CSV)[/dim]")
        console.print(opt_tbl)
        console.print()

    # Issues table
    if result.issues:
        issue_tbl = Table(
            title=f"Issues ({len(result.issues)} found)",
            show_header=True,
            header_style="bold",
            box=rich_box.SIMPLE,
            show_edge=False,
            pad_edge=False,
        )
        issue_tbl.add_column("Row", justify="right", style="dim", no_wrap=True)
        issue_tbl.add_column("Type", no_wrap=True)
        issue_tbl.add_column("Field", style="dim", no_wrap=True)
        issue_tbl.add_column("Value")
        issue_tbl.add_column("Detail")

        _ISSUE_STYLE = {
            "overallocated": "red",
            "zero_allocation": "yellow",
            "missing_allocation": "yellow",
            "malformed_allocation": "red",
            "missing_date": "yellow",
            "malformed_date": "red",
            "unknown_person": "yellow",
            "unknown_client": "yellow",
            "metric_mismatch": "yellow",
        }
        # Limit display to first 25 issues; metric_mismatch issues can be many
        _display_issues = result.issues[:25]
        if len(result.issues) > 25:
            issue_tbl.title = (
                f"Issues ({len(result.issues)} found, showing first 25)"
            )
        for issue in _display_issues:
            label = _ISSUE_LABEL.get(issue.issue_type, issue.issue_type)
            style = _ISSUE_STYLE.get(issue.issue_type, "white")
            issue_tbl.add_row(
                str(issue.row_index + 2),   # +2: 1-based + header row
                f"[{style}]{label}[/{style}]",
                issue.field,
                issue.value,
                f"[dim]{issue.detail}[/dim]",
            )
        console.print(issue_tbl)
        console.print()
    else:
        console.print("[dim]  No issues found.[/dim]")
        console.print()

    # Summary line
    if not result.can_ingest:
        console.print(
            f"[red bold]✗  Missing required fields: "
            f"{', '.join(result.fields_missing)}. Cannot ingest.[/red bold]"
        )
        raise typer.Exit(1)

    warn_count = len([i for i in result.issues if i.issue_type in (
        "unknown_person", "unknown_client", "overallocated",
        "zero_allocation", "missing_date", "metric_mismatch",
    )])
    error_count = len([i for i in result.issues if i.issue_type in (
        "malformed_date", "malformed_allocation",
    )])

    if error_count:
        console.print(
            f"[yellow]⚠  {error_count} error-level issue(s). "
            f"Affected rows will fail during ingest.[/yellow]"
        )
    if warn_count:
        console.print(
            f"[yellow]⚠  {warn_count} warning-level issue(s). "
            f"Review before running manager-os ingest.[/yellow]"
        )
    if not result.issues:
        console.print("[green bold]✓  No issues. Safe to run manager-os ingest.[/green bold]")
    console.print()


# ---------------------------------------------------------------------------
# manager-os profile-deals
# ---------------------------------------------------------------------------

# Issue type → short display label and severity style for the output table.
_DEAL_ISSUE_LABEL: dict[str, str] = {
    "close_date_soon": "close date soon",
    "missing_close_date": "missing close date",
    "malformed_close_date": "malformed close date",
    "missing_sow": "SOW missing",
    "missing_loe": "LOE missing",
    "no_owner": "no owner",
    "unknown_client": "unknown client",
    "high_value_no_staffing": "no staffing info",
    "malformed_probability": "malformed probability",
    "malformed_services_amount": "malformed services $",
    "no_next_steps": "no next steps",
    "stale_status_date": "stale status",
}

_DEAL_ISSUE_STYLE: dict[str, str] = {
    "close_date_soon": "red",
    "missing_close_date": "yellow",
    "malformed_close_date": "red",
    "missing_sow": "red",
    "missing_loe": "yellow",
    "no_owner": "yellow",
    "unknown_client": "yellow",
    "high_value_no_staffing": "yellow",
    "malformed_probability": "red",
    "malformed_services_amount": "red",
    "no_next_steps": "dim",
    "stale_status_date": "yellow",
}


@app.command(name="profile-deals")
def profile_deals(
    path: Optional[str] = typer.Option(
        None,
        "--path",
        help="Path to deals CSV. Defaults to MANAGER_OS_DEALS_CSV.",
    ),
    sample_size: int = typer.Option(
        10,
        "--sample-size",
        help="Number of sample rows to display.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as JSON.",
    ),
) -> None:
    """Validate the configured deals CSV before ingesting real data.

    Reads headers and a sample of rows without writing to the database.
    Shows detected columns, field mapping, close-date proximity alerts,
    missing SOW/LOE status, and any unknown client names compared to
    config/clients.yaml.

    Exits 0 when profiling completes (even with warnings).
    Exits 1 only if the file cannot be read or required columns are missing.
    """
    import json as _json

    from manager_os.config import get_settings, load_clients, load_source_priority
    from manager_os.profile.deals import (
        DealsProfile,
        DealIssue,
        profile_deals_csv,
        _FIELD_DISPLAY as _DEALS_FIELD_DISPLAY,
    )
    from rich import box as rich_box
    from rich.panel import Panel

    settings = get_settings()
    csv_path = path or settings.deals_csv

    if not csv_path:
        console.print("[red]No deals CSV path set. Use --path or set MANAGER_OS_DEALS_CSV.[/red]")
        raise typer.Exit(1)

    # Load config (best-effort — missing config is non-fatal)
    try:
        clients = load_clients(settings)
    except Exception:
        clients = None

    try:
        sp = load_source_priority(settings)
    except Exception:
        sp = None

    # Run the profiler
    try:
        result: DealsProfile = profile_deals_csv(
            csv_path,
            clients=clients,
            source_priority=sp,
            sample_size=sample_size,
        )
    except RuntimeError as exc:
        if json_output:
            console.print_json(_json.dumps({"error": str(exc), "can_ingest": False}))
        else:
            console.print(f"[red]✗  {exc}[/red]")
        raise typer.Exit(1)

    # ── JSON output ──────────────────────────────────────────────────────────
    if json_output:
        console.print_json(_json.dumps(result.to_dict()))
        if not result.can_ingest:
            raise typer.Exit(1)
        return

    # ── Human-readable output ────────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        "[bold]Manager OS — Deals CSV Profile[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))
    console.print(f"  [dim]File:[/dim]    {result.path}")
    console.print(f"  [dim]Rows:[/dim]    {result.total_rows}")
    console.print(f"  [dim]Format:[/dim]  {result.detected_format}")
    console.print(f"  [dim]Sample:[/dim]  {result.sample_size} of {result.total_rows}")
    console.print()

    # Column mapping table
    col_tbl = Table(
        title="Column Mapping",
        show_header=True,
        header_style="bold",
        box=rich_box.SIMPLE,
        show_edge=False,
        pad_edge=False,
    )
    col_tbl.add_column("Raw Column", style="dim", no_wrap=True)
    col_tbl.add_column("Normalised", no_wrap=True)
    col_tbl.add_column("Field", style="cyan", no_wrap=True)

    for raw, norm in result.column_mapping.items():
        display = _DEALS_FIELD_DISPLAY.get(norm, "")
        changed = raw != norm
        norm_display = f"[green]{norm}[/green]" if changed else f"[dim]{norm}[/dim]"
        col_tbl.add_row(raw, norm_display, display)
    console.print(col_tbl)
    console.print()

    # Required fields status
    _REQ = ["account", "deal_name"] if result.detected_format != "netsuite" else ["deal_id", "account"]
    req_tbl = Table(
        title="Required Fields",
        show_header=False,
        box=None,
        pad_edge=False,
        show_edge=False,
    )
    req_tbl.add_column("Status", no_wrap=True)
    req_tbl.add_column("Field")
    for canonical in _REQ:
        display = _DEALS_FIELD_DISPLAY.get(canonical, canonical)
        if canonical in result.fields_found:
            req_tbl.add_row("[green]✓ FOUND[/green]", f"{display} ({canonical})")
        else:
            req_tbl.add_row("[red]✗ MISSING[/red]", f"[red]{display} ({canonical})[/red]")
    # Show deal_name separately for NetSuite (derived)
    if result.detected_format == "netsuite":
        if result.derived_deal_name_count > 0:
            req_tbl.add_row(
                "[green]✓ DERIVED[/green]",
                f"deal name (deal_name) — derived from Customer + Opportunity ID "
                f"[dim]({result.derived_deal_name_count} rows)[/dim]"
            )
        elif "deal_name" in result.fields_found:
            req_tbl.add_row("[green]✓ FOUND[/green]", "deal name (deal_name)")
    else:
        if "deal_name" not in _REQ:
            display = _DEALS_FIELD_DISPLAY.get("deal_name", "deal name")
            if "deal_name" in result.fields_found:
                req_tbl.add_row("[green]✓ FOUND[/green]", f"{display} (deal_name)")
            else:
                req_tbl.add_row("[red]✗ MISSING[/red]", f"[red]{display} (deal_name)[/red]")
    console.print(req_tbl)
    console.print()

    # Optional fields coverage
    _OPT_DISPLAY = [
        ("stage", "stage"),
        ("close_date", "close date"),
        ("forecast_category", "forecast category"),
        ("probability", "probability"),
        ("services_amount", "services ($)"),
        ("last_status_changed_date", "last status changed"),
        ("next_steps", "next steps"),
        ("delivery_comment", "delivery comment"),
        ("technical_owner", "owner"),
        ("ae_name", "AE/ECA"),
        ("loe_status", "LOE status"),
        ("sow_status", "SOW status"),
    ]
    opt_tbl = Table(
        title="Optional Fields",
        show_header=False,
        box=None,
        pad_edge=False,
        show_edge=False,
    )
    opt_tbl.add_column("Status", no_wrap=True)
    opt_tbl.add_column("Field")
    for canonical, display in _OPT_DISPLAY:
        if canonical in result.fields_found:
            opt_tbl.add_row("[green]✓[/green]", f"{display} ({canonical})")
        else:
            opt_tbl.add_row("[dim]–[/dim]", f"[dim]{display} (not in CSV)[/dim]")
    console.print(opt_tbl)
    console.print()

    # NetSuite-specific summary table
    if result.detected_format == "netsuite" and result.netsuite_summary:
        ns = result.netsuite_summary
        ns_tbl = Table(
            title="NetSuite Summary",
            show_header=False,
            box=None,
            pad_edge=False,
            show_edge=False,
        )
        ns_tbl.add_column("Metric", style="dim", no_wrap=True)
        ns_tbl.add_column("Value", justify="right")
        ns_tbl.add_row("Deal names derived", str(ns.get("derived_deal_names", 0)))
        ns_tbl.add_row("Closing within 14 days", str(ns.get("close_date_soon", 0)))
        ns_tbl.add_row("Stale status (>30 days)", str(ns.get("stale_status_date", 0)))
        ns_tbl.add_row("Missing next steps", str(ns.get("no_next_steps", 0)))
        if ns.get("malformed_close_date", 0):
            ns_tbl.add_row("[red]Malformed close date[/red]", str(ns["malformed_close_date"]))
        if ns.get("malformed_probability", 0):
            ns_tbl.add_row("[red]Malformed probability[/red]", str(ns["malformed_probability"]))
        if ns.get("malformed_services_amount", 0):
            ns_tbl.add_row("[red]Malformed services $[/red]", str(ns["malformed_services_amount"]))
        console.print(ns_tbl)
        console.print()

    # Issues table
    if result.issues:
        _warn_issues = [i for i in result.issues if getattr(i, 'severity', 'warning') != 'info']
        _info_issues = [i for i in result.issues if getattr(i, 'severity', 'warning') == 'info']
        _display_issues = _warn_issues[:25] + _info_issues[:5]
        title_suffix = f" ({len(result.issues)} found)"
        if len(result.issues) > len(_display_issues):
            title_suffix = f" ({len(result.issues)} found, showing {len(_display_issues)})"
        issue_tbl = Table(
            title=f"Issues{title_suffix}",
            show_header=True,
            header_style="bold",
            box=rich_box.SIMPLE,
            show_edge=False,
            pad_edge=False,
        )
        issue_tbl.add_column("Row", justify="right", style="dim", no_wrap=True)
        issue_tbl.add_column("Type", no_wrap=True)
        issue_tbl.add_column("Field", style="dim", no_wrap=True)
        issue_tbl.add_column("Value")
        issue_tbl.add_column("Detail")

        for issue in _display_issues:
            label = _DEAL_ISSUE_LABEL.get(issue.issue_type, issue.issue_type)
            style = _DEAL_ISSUE_STYLE.get(issue.issue_type, "white")
            issue_tbl.add_row(
                str(issue.row_index + 2),   # +2: 1-based + header row
                f"[{style}]{label}[/{style}]",
                issue.field,
                issue.value,
                f"[dim]{issue.detail}[/dim]",
            )
        console.print(issue_tbl)
        console.print()
    else:
        console.print("[dim]  No issues found.[/dim]")
        console.print()

    # Summary line
    if not result.can_ingest:
        console.print(
            f"[red bold]✗  Missing required fields: "
            f"{', '.join(result.fields_missing)}. Cannot ingest.[/red bold]"
        )
        raise typer.Exit(1)

    error_types = {"malformed_close_date", "malformed_probability", "malformed_services_amount"}
    warn_types = {
        "close_date_soon", "missing_close_date", "missing_sow", "missing_loe",
        "no_owner", "unknown_client", "high_value_no_staffing", "stale_status_date",
    }
    error_count = len([i for i in result.issues if i.issue_type in error_types])
    warn_count = len([i for i in result.issues if i.issue_type in warn_types])

    if error_count:
        console.print(
            f"[yellow]⚠  {error_count} error-level issue(s). "
            f"Affected rows will fail during ingest.[/yellow]"
        )
    if warn_count:
        console.print(
            f"[yellow]⚠  {warn_count} warning-level issue(s). "
            f"Review before running manager-os ingest.[/yellow]"
        )
    if not result.issues:
        console.print("[green bold]✓  No issues. Safe to run manager-os ingest.[/green bold]")
    console.print()


# ---------------------------------------------------------------------------
# signal-rate
# ---------------------------------------------------------------------------

@app.command("signal-rate")
def signal_rate(
    signal_id: str = typer.Argument(..., help="ID of the signal to rate."),
    rating: str = typer.Argument(..., help="Rating: useful | not_useful | duplicate | wrong_entity | too_low_priority | snoozed | resolved"),
    note: Optional[str] = typer.Option(None, "--note", help="Optional free-text note about this rating."),
    snooze_until: Optional[str] = typer.Option(
        None,
        "--snooze-until",
        metavar="YYYY-MM-DD",
        help="Date until which to snooze the signal (only valid for rating='snoozed').",
    ),
) -> None:
    """Rate a signal's usefulness to measure Manager OS output quality."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.signal_feedback import rate_signal, VALID_RATINGS

    if rating not in VALID_RATINGS:
        console.print(f"[red]Invalid rating {rating!r}.[/red]")
        console.print(f"Valid values: {', '.join(sorted(VALID_RATINGS))}")
        raise typer.Exit(1)

    snooze_date: Optional[date] = None
    if snooze_until:
        try:
            snooze_date = date.fromisoformat(snooze_until)
        except ValueError:
            console.print(f"[red]Invalid --snooze-until date {snooze_until!r}. Use YYYY-MM-DD.[/red]")
            raise typer.Exit(1)

    settings = get_settings()
    conn = get_connection(settings.db_path)

    try:
        rate_signal(conn, signal_id=signal_id, rating=rating, note=note, snooze_until=snooze_date)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green]  Signal [bold]{signal_id[:16]}...[/bold] rated [bold]{rating}[/bold]")
    if note:
        console.print(f"   Note: {note}")
    if snooze_date:
        console.print(f"   Snoozed until: {snooze_date}")


# ---------------------------------------------------------------------------
# signal-feedback
# ---------------------------------------------------------------------------

@app.command("signal-feedback")
def signal_feedback() -> None:
    """Show a concise signal usefulness report."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.signal_feedback import get_feedback_report

    settings = get_settings()
    conn = get_connection(settings.db_path)
    report = get_feedback_report(conn)

    console.print()
    console.print("[bold cyan]Signal Usefulness Report[/bold cyan]")
    console.print("─" * 40)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Unrated open signals", str(report["unrated_open"]))
    console.print(table)
    console.print()

    rated_table = Table(show_header=True, header_style="bold")
    rated_table.add_column("Rating", style="dim")
    rated_table.add_column("Count", justify="right")

    rated_table.add_row("useful", str(report["useful"]))
    rated_table.add_row("not_useful", str(report["not_useful"]))
    rated_table.add_row("duplicate", str(report["duplicate"]))
    rated_table.add_row("wrong_entity", str(report["wrong_entity"]))
    rated_table.add_row("too_low_priority", str(report["too_low_priority"]))
    rated_table.add_row("snoozed", str(report["snoozed"]))
    rated_table.add_row("resolved", str(report["resolved"]))
    rated_table.add_row("─" * 20, "─" * 6)
    rated_table.add_row("[bold]Total rated[/bold]", f"[bold]{report['total_rated']}[/bold]")
    console.print(rated_table)
    console.print()

    usefulness_pct = report["usefulness_pct"]
    color = "green" if usefulness_pct >= 70 else ("yellow" if usefulness_pct >= 40 else "red")
    console.print(
        f"[bold]Usefulness:[/bold] [{color}]{usefulness_pct:.0f}%[/{color}]"
        f"  (useful / rated, excluding snoozed + resolved)"
    )

    if report["top_rejection_reasons"]:
        console.print()
        console.print("[bold]Top rejection reasons:[/bold]")
        for reason, count in report["top_rejection_reasons"]:
            console.print(f"  {reason}: {count}")
    console.print()


# ---------------------------------------------------------------------------
# config-audit
# ---------------------------------------------------------------------------

@app.command("config-audit")
def config_audit(
    real_data_preview: bool = typer.Option(
        False,
        "--real-data-preview",
        help="Scan vault and generate a suggestion report (no config writes).",
    ),
    vault_path_override: Optional[str] = typer.Option(
        None,
        "--vault-path",
        help="Override MANAGER_OS_VAULT_PATH for this scan.",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Maximum number of notes to scan.",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Print summary as JSON instead of Rich text.",
    ),
    include_body_signals: bool = typer.Option(
        False,
        "--include-body-signals",
        help="Also scan body text for additional candidates (light scan only; no body in output).",
    ),
) -> None:
    """Scan Obsidian vault metadata and suggest config entries. Read-only; never modifies config."""
    import json as _json
    from datetime import date as _date
    from pathlib import Path as _Path

    from manager_os.config import get_settings, load_people, load_clients, load_deal_aliases
    from manager_os.build.config_audit import scan_vault, render_report

    if not real_data_preview:
        console.print("[yellow]Use --real-data-preview to run the scan.[/yellow]")
        console.print("Example:")
        console.print("  manager-os config-audit --real-data-preview")
        raise typer.Exit(0)

    settings = get_settings()

    # Determine vault path
    vault = vault_path_override or settings.vault_path
    if not vault:
        console.print(
            "[red]No vault path configured. "
            "Set MANAGER_OS_VAULT_PATH in .env or use --vault-path.[/red]"
        )
        raise typer.Exit(1)

    # Safety: output/ must be gitignored
    output_root = _Path("output")
    gitignore = _Path(".gitignore")
    if gitignore.exists():
        gitignore_text = gitignore.read_text(encoding="utf-8")
        if "output/" not in gitignore_text and "output" not in gitignore_text:
            console.print(
                "[red]Safety check failed: output/ is not listed in .gitignore. "
                "Add 'output/' to .gitignore before running config-audit.[/red]"
            )
            raise typer.Exit(1)
    else:
        console.print("[yellow]Warning: no .gitignore found. Proceeding, but ensure output/ is excluded from git.[/yellow]")

    # Load existing config for gap analysis
    try:
        people = load_people(settings)
        existing_people = [p.name for p in people]
    except Exception:
        existing_people = []
    try:
        clients = load_clients(settings)
        existing_clients = [c.name for c in clients]
    except Exception:
        existing_clients = []
    try:
        deal_aliases = load_deal_aliases(settings)
        existing_deals = list(deal_aliases.values())
    except Exception:
        existing_deals = []

    console.print(f"[dim]Scanning vault: {vault}[/dim]")
    if limit:
        console.print(f"[dim]Limit: {limit} notes[/dim]")

    try:
        result = scan_vault(
            vault_path=vault,
            existing_people=existing_people,
            existing_clients=existing_clients,
            existing_deals=existing_deals,
            limit=limit,
            include_body_signals=include_body_signals,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    # Write report
    today = _date.today()
    report_dir = output_root / "config_audit"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{today.isoformat()}-real-data-preview.md"
    report_text = render_report(result, report_date=today)
    report_path.write_text(report_text, encoding="utf-8")

    if output_json:
        summary = {
            "notes_scanned": result.notes_scanned,
            "notes_skipped": result.notes_skipped,
            "candidate_people": len(result.candidate_people),
            "candidate_clients": len(result.candidate_clients),
            "candidate_deals": len(result.candidate_deals),
            "config_gaps": len(result.config_gaps),
            "report_path": str(report_path),
        }
        console.print(_json.dumps(summary, indent=2))
    else:
        console.print()
        console.print(f"[green]Report written to:[/green] {report_path}")
        console.print()

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Metric", style="dim")
        table.add_column("Value", style="bold")
        table.add_row("Notes scanned", str(result.notes_scanned))
        table.add_row("Notes skipped", str(result.notes_skipped))
        table.add_row("Candidate people", str(len(result.candidate_people)))
        table.add_row("Candidate clients", str(len(result.candidate_clients)))
        table.add_row("Candidate deals", str(len(result.candidate_deals)))
        table.add_row("Config gaps", str(len(result.config_gaps)))
        console.print(table)
        console.print()
        console.print(
            "[yellow bold]⚠  This report may contain real names from your vault. "
            "Do not commit output/config_audit/.[/yellow bold]"
        )
        console.print()


# ---------------------------------------------------------------------------
# feedback  (sub-command group: list / mark / summary)
# ---------------------------------------------------------------------------

feedback_app = typer.Typer(help="Manage brief item feedback.")
app.add_typer(feedback_app, name="feedback")


@feedback_app.command("list")
def feedback_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent entries to show."),
) -> None:
    """List recent feedback entries."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.feedback import list_feedback

    settings = get_settings()
    conn = get_connection(settings.db_path)
    entries = list_feedback(conn, limit=limit)

    if not entries:
        console.print("[dim]No feedback recorded yet.[/dim]")
        console.print(
            "  Use [bold]manager-os feedback mark <item_id> <rating>[/bold] "
            "to record feedback from the daily brief.\n"
            "  Item IDs appear in the brief as [signal:abc123], [action:def456], etc."
        )
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Item ID", style="cyan", no_wrap=True)
    table.add_column("Rating", style="bold")
    table.add_column("Reason", overflow="fold")
    table.add_column("When", style="dim")

    _RATING_STYLE = {
        "useful":          "green",
        "noisy":           "yellow",
        "stale":           "dim",
        "wrong":           "red",
        "missing-context": "blue",
    }
    for e in entries:
        style = _RATING_STYLE.get(e["rating"], "")
        when = ""
        if e["created_at"]:
            try:
                when = str(e["created_at"])[:16]
            except Exception:
                when = str(e["created_at"])
        table.add_row(
            e["item_id"],
            f"[{style}]{e['rating']}[/{style}]" if style else e["rating"],
            e["reason"] or "",
            when,
        )
    console.print(table)
    console.print()


@feedback_app.command("mark")
def feedback_mark(
    item_id: str = typer.Argument(
        ...,
        help="Brief item ID from the daily brief, e.g. signal:abc123, deal:OPP025010",
    ),
    rating: str = typer.Argument(
        ...,
        help="Rating: useful | noisy | stale | wrong | missing-context",
    ),
    reason: Optional[str] = typer.Option(
        None, "--reason", "-r", help="Optional free-text reason."
    ),
) -> None:
    """Mark a brief item with a feedback rating.

    Item IDs appear in the daily brief as [signal:abc123], [action:def456],
    [deal:OPP025010], [waiting:abc123], or [decision:abc123].

    Examples:

      manager-os feedback mark signal:abc123 noisy --reason "generic historical note"

      manager-os feedback mark deal:OPP025010 useful
    """
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.feedback import mark, VALID_RATINGS

    if rating not in VALID_RATINGS:
        console.print(f"[red]Invalid rating {rating!r}.[/red]")
        console.print(f"Valid values: {', '.join(sorted(VALID_RATINGS))}")
        raise typer.Exit(1)

    settings = get_settings()
    conn = get_connection(settings.db_path)

    try:
        mark(conn, item_id=item_id, rating=rating, reason=reason)
    except Exception as exc:
        console.print(f"[red]Error recording feedback: {exc}[/red]")
        raise typer.Exit(1)

    _RATING_STYLE = {
        "useful": "green", "noisy": "yellow", "stale": "dim",
        "wrong": "red", "missing-context": "blue",
    }
    style = _RATING_STYLE.get(rating, "bold")
    console.print(
        f"[{style}]✓[/{style}]  [{style}]{item_id}[/{style}] → "
        f"[{style}]{rating}[/{style}]"
        + (f"  [dim]({reason})[/dim]" if reason else "")
    )


@feedback_app.command("summary")
def feedback_summary() -> None:
    """Show a summary of all recorded feedback."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.feedback import get_feedback_summary

    settings = get_settings()
    conn = get_connection(settings.db_path)
    s = get_feedback_summary(conn)

    console.print()
    console.print("[bold cyan]Feedback Summary[/bold cyan]")
    console.print("─" * 44)

    # Counts by rating
    rating_table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    rating_table.add_column("Rating")
    rating_table.add_column("Count", justify="right")

    _STYLES = {
        "useful": "green", "noisy": "yellow", "stale": "dim",
        "wrong": "red", "missing-context": "blue",
    }
    for rating, count in s["counts_by_rating"].items():
        style = _STYLES.get(rating, "")
        rating_table.add_row(
            f"[{style}]{rating}[/{style}]" if style else rating,
            str(count),
        )
    rating_table.add_row("[dim]──────[/dim]", "[dim]─────[/dim]")
    rating_table.add_row("[bold]Total[/bold]", f"[bold]{s['total']}[/bold]")
    console.print(rating_table)

    def _show_top(title: str, rows: list, col_a: str = "Source") -> None:
        if not rows:
            return
        console.print()
        console.print(f"[bold]{title}[/bold]")
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column(col_a, style="dim")
        t.add_column("Count", justify="right")
        for src, n in rows:
            t.add_row(str(src), str(n))
        console.print(t)

    _show_top("Top noisy sources",   s["top_noisy_sources"])
    _show_top("Top stale sources",   s["top_stale_sources"])
    _show_top("Top wrong types",     s["top_wrong_types"],  col_a="Signal type")
    _show_top("Useful item types",   s["useful_types"],     col_a="Item type")
    console.print()


# ---------------------------------------------------------------------------
# action  (sub-command group: list / complete / stale / dismiss / snooze / reopen)
# ---------------------------------------------------------------------------

action_app = typer.Typer(help="Manage open action items.")
app.add_typer(action_app, name="action")

_ACTION_SELECT = """
    SELECT id, assigned_to, description, due_date, status,
           feedback_rating, feedback_reason, snooze_until, created_at
    FROM action_items
"""


def _print_action_table(rows) -> None:
    """Render action items as a Rich table."""
    if not rows:
        console.print("[dim]No matching action items.[/dim]")
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Brief ID", style="cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Assigned to", style="dim")
    table.add_column("Description", overflow="fold")
    table.add_column("Due", style="dim")
    table.add_column("Feedback", style="dim")
    _STATUS_STYLE = {
        "open": "green", "completed": "dim", "stale": "dim",
        "dismissed": "dim", "snoozed": "yellow", "not_mine": "dim",
    }
    for r in rows:
        ai_id, assigned_to, desc, due, status, fb_rating, fb_reason, snooze, _ = r
        brief_id = f"action:{ai_id[:16]}"
        sty = _STATUS_STYLE.get(status, "")
        fb_str = fb_rating or ""
        if fb_reason:
            fb_str += f" ({fb_reason[:30]})"
        table.add_row(
            brief_id,
            f"[{sty}]{status}[/{sty}]" if sty else status,
            assigned_to or "",
            desc[:80] + ("…" if len(desc) > 80 else ""),
            str(due) if due else "",
            fb_str,
        )
    console.print(table)


@action_app.command("list")
def action_list(
    status_filter: str = typer.Option(
        "open",
        "--status", "-s",
        help="Filter by status: open, completed, stale, all",
    ),
    limit: int = typer.Option(30, "--limit", "-n"),
) -> None:
    """List action items from the database."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection

    settings = get_settings()
    conn = get_connection(settings.db_path)

    if status_filter == "all":
        where = ""
        params: list = []
    else:
        statuses = [s.strip() for s in status_filter.split(",")]
        placeholders = ", ".join("?" * len(statuses))
        where = f"WHERE status IN ({placeholders})"
        params = statuses

    rows = conn.execute(
        _ACTION_SELECT + where + " ORDER BY due_date NULLS LAST LIMIT ?",
        params + [limit],
    ).fetchall()

    console.print()
    console.print(f"[bold cyan]Action Items[/bold cyan]  [dim](filter: {status_filter})[/dim]")
    console.print("─" * 60)
    _print_action_table(rows)
    console.print()


def _action_update(item_id: str, status: str, reason: Optional[str] = None) -> None:
    """Shared helper: update one action item by brief-id or raw id."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.dashboard_data import update_action_item

    settings = get_settings()
    conn = get_connection(settings.db_path)

    # Accept both "action:abc123" and raw 16-char prefix
    raw_id = item_id.removeprefix("action:")
    # Find by prefix (since brief IDs are truncated to 16 chars)
    row = conn.execute(
        "SELECT id FROM action_items WHERE id LIKE ?", [raw_id + "%"]
    ).fetchone()
    if row is None:
        console.print(f"[red]Action item not found: {item_id!r}[/red]")
        raise typer.Exit(1)
    full_id = row[0]
    update_action_item(conn, full_id, status=status, feedback_reason=reason)
    console.print(f"[green]✓[/green]  [cyan]action:{full_id[:16]}[/cyan] → [bold]{status}[/bold]"
                  + (f"  [dim]({reason})[/dim]" if reason else ""))


@action_app.command("complete")
def action_complete(
    item_id: str = typer.Argument(..., help="Brief ID (action:abc123) or raw action item ID."),
) -> None:
    """Mark an action item as completed."""
    _action_update(item_id, "completed")


@action_app.command("stale")
def action_stale(
    item_id: str = typer.Argument(...),
    reason: Optional[str] = typer.Option(None, "--reason", "-r"),
) -> None:
    """Mark an action item as stale (old/no longer relevant)."""
    _action_update(item_id, "stale", reason)


@action_app.command("dismiss")
def action_dismiss(
    item_id: str = typer.Argument(...),
    reason: Optional[str] = typer.Option(None, "--reason", "-r"),
) -> None:
    """Dismiss an action item (not relevant / not mine)."""
    _action_update(item_id, "dismissed", reason)


@action_app.command("snooze")
def action_snooze(
    item_id: str = typer.Argument(...),
    until: str = typer.Option(..., "--until", metavar="YYYY-MM-DD", help="Snooze until this date."),
) -> None:
    """Snooze an action item until a future date."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.dashboard_data import update_action_item

    try:
        snooze_date = date.fromisoformat(until)
    except ValueError:
        console.print(f"[red]Invalid date {until!r}. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)

    settings = get_settings()
    conn = get_connection(settings.db_path)
    raw_id = item_id.removeprefix("action:")
    row = conn.execute("SELECT id FROM action_items WHERE id LIKE ?", [raw_id + "%"]).fetchone()
    if row is None:
        console.print(f"[red]Action item not found: {item_id!r}[/red]")
        raise typer.Exit(1)
    update_action_item(conn, row[0], status="snoozed", snooze_until=snooze_date)
    console.print(f"[yellow]💤[/yellow]  [cyan]action:{row[0][:16]}[/cyan] snoozed until [bold]{snooze_date}[/bold]")


@action_app.command("reopen")
def action_reopen(
    item_id: str = typer.Argument(...),
) -> None:
    """Reopen a completed, stale, or dismissed action item."""
    _action_update(item_id, "open")


# ---------------------------------------------------------------------------
# scope-preview
# ---------------------------------------------------------------------------

@app.command("scope-preview")
def scope_preview(
    vault_path: Optional[str] = typer.Option(
        None,
        "--vault-path",
        help="Override MANAGER_OS_VAULT_PATH for this scan.",
    ),
    show_signal: bool = typer.Option(False, "--show-signal", help="List signal-tier notes."),
    show_context: bool = typer.Option(False, "--show-context", help="List context-tier notes."),
    show_excluded: bool = typer.Option(False, "--show-excluded", help="List excluded notes."),
    show_stale: bool = typer.Option(False, "--show-stale", help="List stale notes only."),
) -> None:
    """Preview source tier classification across the Obsidian vault.

    Reads the vault directly (no database required) and shows how many
    notes fall into each tier: signal, context, or excluded.
    """
    from pathlib import Path as _Path

    from manager_os.config import get_settings
    from manager_os.scope import walk_vault, load_source_scope
    from rich import box as rich_box
    from rich.panel import Panel

    settings = get_settings()
    vault = vault_path or settings.vault_path

    if not vault:
        console.print("[red]No vault path configured. Set MANAGER_OS_VAULT_PATH or use --vault-path.[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]Scanning: {vault}[/dim]")
    report = walk_vault(vault)

    console.print()
    console.print(Panel.fit(
        "[bold]Manager OS — Scope Preview[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))
    console.print(f"  [dim]Vault:[/dim]   {report.vault_path}")
    console.print(f"  [dim]Total .md notes found:[/dim]  {report.total_notes}")
    console.print()

    # Summary table
    tbl = Table(
        title="Tier Distribution",
        show_header=True,
        header_style="bold",
        box=rich_box.SIMPLE,
    )
    tbl.add_column("Tier", style="bold")
    tbl.add_column("Count", justify="right")
    tbl.add_column("%", justify="right", style="dim")
    total = max(report.total_notes, 1)
    tbl.add_row("[green]Signal[/green]",   str(report.signal_count),
                f"{report.signal_count/total*100:.0f}%")
    tbl.add_row("[blue]Context[/blue]",    str(report.context_count),
                f"{report.context_count/total*100:.0f}%")
    tbl.add_row("[dim]Excluded[/dim]",     str(report.excluded_count),
                f"{report.excluded_count/total*100:.0f}%")
    tbl.add_row("[dim]───[/dim]", "[dim]───[/dim]", "[dim]───[/dim]")
    tbl.add_row("[bold]Total[/bold]", f"[bold]{report.total_notes}[/bold]", "")
    console.print(tbl)
    console.print()

    # Flags
    flags_tbl = Table(show_header=False, box=None, padding=(0, 2))
    flags_tbl.add_column("Flag", style="dim")
    flags_tbl.add_column("Count", justify="right")
    flags_tbl.add_row("Stale notes (> max_age)",        str(report.stale_count))
    flags_tbl.add_row("Active overrides (fm forces signal)", str(report.active_override_count))
    flags_tbl.add_row("Frontmatter-excluded notes",      str(report.fm_excluded_count))
    console.print(flags_tbl)
    console.print()

    # Top reasons
    if report.top_reasons:
        reason_tbl = Table(
            title="Top Scope Reasons",
            show_header=True,
            header_style="bold",
            box=rich_box.SIMPLE,
        )
        reason_tbl.add_column("Reason", style="dim")
        reason_tbl.add_column("Count", justify="right")
        for reason, count in report.top_reasons:
            reason_tbl.add_row(reason, str(count))
        console.print(reason_tbl)
        console.print()

    # Folders by tier
    for tier, label in [("signal", "Top Signal Folders"), ("context", "Top Context Folders"), ("excluded", "Top Excluded Folders")]:
        folders = report.folders_by_tier.get(tier, {})
        if folders:
            ft = Table(title=label, show_header=True, header_style="bold", box=rich_box.SIMPLE)
            ft.add_column("Folder", style="dim")
            ft.add_column("Count", justify="right")
            for folder, count in list(folders.items())[:10]:
                ft.add_row(folder, str(count))
            console.print(ft)
            console.print()

    # Detail listings
    if show_signal:
        console.print(f"[green bold]Signal notes ({report.signal_count}):[/green bold]")
        for p in report.signal_paths:
            console.print(f"  [green]•[/green] {p}")
        console.print()

    if show_context:
        console.print(f"[blue bold]Context notes ({report.context_count}):[/blue bold]")
        for p in report.context_paths:
            console.print(f"  [blue]•[/blue] {p}")
        console.print()

    if show_excluded:
        console.print(f"[dim bold]Excluded notes ({report.excluded_count}):[/dim bold]")
        for p in report.excluded_paths:
            console.print(f"  [dim]•[/dim] {p}")
        console.print()

    if show_stale:
        console.print(f"[yellow bold]Stale notes ({report.stale_count}):[/yellow bold]")
        for p in report.stale_paths:
            console.print(f"  [yellow]•[/yellow] {p}")
        console.print()


# ---------------------------------------------------------------------------
# workspace-doctor
# ---------------------------------------------------------------------------

@app.command("workspace-doctor")
def workspace_doctor_cmd() -> None:
    """Diagnose workspace retrieval configuration."""
    from manager_os.ingest.workspace_gemini import workspace_doctor
    from rich import box as rich_box
    from rich.panel import Panel
    from rich.table import Table

    console.print("[dim]Checking workspace retrieval configuration…[/dim]")
    result = workspace_doctor()

    console.print()
    console.print(Panel.fit(
        "[bold]Manager OS — Workspace Doctor[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Setting", style="dim")
    tbl.add_column("Value")
    tbl.add_row("Gemini CLI available", "[green]yes[/green]" if result.gemini_available else "[red]no[/red]")
    tbl.add_row("YOLO configured", f"[green]{result.yolo_configured}[/green]")
    tbl.add_row("Retrieval enabled", f"[green]{result.retrieval_enabled}[/green]")
    tbl.add_row("Forecast retrieval", f"[green]{result.forecast_enabled}[/green]")
    tbl.add_row("Calendar retrieval", f"[green]{result.calendar_enabled}[/green]")
    tbl.add_row("Activity retrieval", f"[green]{result.activity_enabled}[/green]")
    console.print(tbl)

    if result.errors:
        console.print()
        console.print("[yellow]Issues:[/yellow]")
        for e in result.errors:
            console.print(f"  [yellow]⚠ {e}[/yellow]")

    console.print()


# ---------------------------------------------------------------------------
# env-audit
# ---------------------------------------------------------------------------

@app.command("env-audit")
def env_audit_cmd(
    fix_local: bool = typer.Option(False, "--fix-local", help="Add missing vars to local .env without overwriting."),
    example_only: bool = typer.Option(False, "--example-only", help="Only check .env.example, ignore local .env."),
    as_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
) -> None:
    """Audit environment variables against code and .env.example."""
    from manager_os.env_audit import run_audit
    
    result, exit_code = run_audit(fix_local=fix_local, example_only=example_only, as_json=as_json)
    raise typer.Exit(code=exit_code)


# ---------------------------------------------------------------------------
# workspace-fetch commands
# ---------------------------------------------------------------------------

def _default_output_dir() -> str | None:
    return None  # uses data/raw/workspace_snapshots/<subdir>/


def _do_workspace_fetch(
    command_name: str,
    retrieve_fn,
    target_date: date,
    dry_run: bool,
    print_prompt: bool,
    no_yolo: bool,
    timeout: int,
    output_dir: str | None,
    **kwargs,
) -> None:
    """Run a workspace retrieval command and display results."""
    from rich import box as rich_box
    from rich.panel import Panel

    use_yolo = not no_yolo

    if dry_run:
        console.print(f"[dim]Dry run — will not contact Google Workspace[/dim]")

    result = retrieve_fn(
        target_date=target_date,
        use_yolo=use_yolo,
        timeout=timeout,
        dry_run=dry_run,
        output_dir=output_dir or _default_output_dir(),
        **kwargs,
    )

    if print_prompt or (dry_run and result.json_text):
        console.print()
        console.print(Panel.fit(
            "[bold]Prompt sent to Gemini[/bold]",
            box=rich_box.ROUNDED,
            border_style="yellow",
        ))
        console.print(result.json_text[:2000])
        console.print()

    if dry_run:
        console.print(f"[yellow bold]⚠ Dry run — nothing was retrieved or written.[/yellow bold]")
        return

    if not result.ok:
        console.print(f"[red]Retrieval failed: {result.error}[/red]")
        return

    console.print(f"[green]✓ Retrieved {len(result.items)} item(s)[/green]")
    if result.source_title:
        console.print(f"  Source: {result.source_title}")
    if result.written_to:
        console.print(f"  Snapshot: {result.written_to}")
    
    # Phase 3: Show better snapshot metadata for activity
    if command_name == "activity" and result.ok:
        try:
            import json
            with open(result.written_to, 'r') as f:
                data = json.load(f)
            ai_count = data.get("action_items_count", len(data.get("action_items", [])))
            attn_count = data.get("requires_attention_count", 0)
            console.print(f"  Action items: {ai_count} (Requires attention: {attn_count})")
        except Exception:
            pass
            
    console.print()


@app.command("workspace-fetch-forecast")
def workspace_fetch_forecast(
    target_date: Optional[str] = typer.Option(None, "--date"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print prompt without running."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Show the Gemini prompt."),
    no_yolo: bool = typer.Option(False, "--no-yolo", help="Disable YOLO mode."),
    timeout: int = typer.Option(180, "--timeout", help="Timeout in seconds."),
    output_dir: Optional[str] = typer.Option(None, "--output-dir"),
    doc_name: Optional[str] = typer.Option(None, "--doc", help="Document name or URL to search for (e.g. 'Delta-12 Forecast' or a Google Sheets URL)."),
) -> None:
    """Retrieve latest staffing forecast from Google Workspace."""
    from manager_os.ingest.workspace_gemini import retrieve_forecast

    run_date = date.fromisoformat(target_date) if target_date else date.today()
    query_hint = ""
    if doc_name:
        query_hint = f"Look for a document named '{doc_name}' or at URL containing '{doc_name}'."
    _do_workspace_fetch("forecast", retrieve_forecast, run_date, dry_run, print_prompt, no_yolo, timeout, output_dir, query_hint=query_hint)


@app.command("workspace-fetch-calendar")
def workspace_fetch_calendar(
    target_date: Optional[str] = typer.Option(None, "--date"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print prompt without running."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Show the Gemini prompt."),
    no_yolo: bool = typer.Option(False, "--no-yolo", help="Disable YOLO mode."),
    timeout: int = typer.Option(180, "--timeout", help="Timeout in seconds."),
    output_dir: Optional[str] = typer.Option(None, "--output-dir"),
    lookback: Optional[int] = typer.Option(None, "--lookback", help="Override lookback days."),
    lookahead: Optional[int] = typer.Option(None, "--lookahead", help="Override lookahead days."),
) -> None:
    """Retrieve calendar events from Google Workspace."""
    from manager_os.ingest.workspace_gemini import retrieve_calendar

    run_date = date.fromisoformat(target_date) if target_date else date.today()
    _do_workspace_fetch(
        "calendar", retrieve_calendar, run_date, dry_run, print_prompt, no_yolo, timeout, output_dir,
        lookback_days=lookback, lookahead_days=lookahead,
    )


@app.command("workspace-fetch-activity")
def workspace_fetch_activity(
    target_date: Optional[str] = typer.Option(None, "--date"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print prompt without running."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Show the Gemini prompt."),
    no_yolo: bool = typer.Option(False, "--no-yolo", help="Disable YOLO mode."),
    timeout: int = typer.Option(180, "--timeout", help="Timeout in seconds."),
    output_dir: Optional[str] = typer.Option(None, "--output-dir"),
    lookback: Optional[int] = typer.Option(None, "--lookback", help="Override lookback days."),
    chat_url: Optional[str] = typer.Option(None, "--chat-url", help="Override the configured Google Chat URL."),
) -> None:
    """Retrieve workspace activity summary from configured Google Chat space."""
    from manager_os.ingest.workspace_gemini import retrieve_activity

    run_date = date.fromisoformat(target_date) if target_date else date.today()
    _do_workspace_fetch(
        "activity", retrieve_activity, run_date, dry_run, print_prompt, no_yolo, timeout, output_dir,
        lookback_days=lookback, chat_url=chat_url,
    )


@app.command("workspace-fetch-all")
def workspace_fetch_all(
    target_date: Optional[str] = typer.Option(None, "--date"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print prompts without running."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Show the Gemini prompts."),
    no_yolo: bool = typer.Option(False, "--no-yolo", help="Disable YOLO mode."),
    timeout: int = typer.Option(180, "--timeout", help="Timeout in seconds per source."),
    output_dir: Optional[str] = typer.Option(None, "--output-dir"),
) -> None:
    """Retrieve all workspace sources (forecast, calendar, activity)."""
    from manager_os.ingest.workspace_gemini import (
        retrieve_forecast,
        retrieve_calendar,
        retrieve_activity,
    )

    run_date = date.fromisoformat(target_date) if target_date else date.today()
    use_yolo = not no_yolo

    if dry_run:
        console.print("[dim]Dry run — will not contact Google Workspace[/dim]")

    console.print("[bold]Forecast:[/bold]")
    _do_workspace_fetch("forecast", retrieve_forecast, run_date, dry_run, print_prompt, no_yolo, timeout, output_dir)

    console.print("[bold]Calendar:[/bold]")
    _do_workspace_fetch(
        "calendar", retrieve_calendar, run_date, dry_run, print_prompt, no_yolo, timeout, output_dir,
    )

    console.print("[bold]Activity:[/bold]")
    _do_workspace_fetch(
        "activity", retrieve_activity, run_date, dry_run, print_prompt, no_yolo, timeout, output_dir,
    )

    console.print("[green]All sources processed.[/green]")


# ---------------------------------------------------------------------------
# llm-doctor
# ---------------------------------------------------------------------------

@app.command("llm-doctor")
def llm_doctor(
    smoke_test: bool = typer.Option(True, "--smoke-test/--no-smoke-test", help="Send a minimal prompt to verify the binary works."),
    timeout: int = typer.Option(60, "--timeout", "-t", help="Timeout in seconds for the smoke test."),
) -> None:
    """Diagnose the Gemini CLI provider configuration."""
    from manager_os.llm.gemini_cli import run_doctor
    from rich import box as rich_box
    from rich.panel import Panel

    console.print("[dim]Running Gemini CLI diagnostics…[/dim]")
    result = run_doctor(smoke_test=smoke_test, timeout=timeout)

    console.print()
    console.print(Panel.fit(
        "[bold]Manager OS — LLM Doctor[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Setting", style="dim")
    tbl.add_column("Value")
    tbl.add_row("Provider", result.provider)
    tbl.add_row("LLM enabled", f"[green]true[/green]" if result.llm_enabled else "[red]false[/red]")
    tbl.add_row("Gemini binary", result.gemini_bin)
    tbl.add_row("Binary exists", f"[green]yes[/green]" if result.gemini_bin_exists else "[red]no[/red]")
    tbl.add_row("Binary executable", f"[green]yes[/green]" if result.gemini_bin_executable else "[red]no[/red]")
    tbl.add_row("Configured model", result.configured_model)
    tbl.add_row("Base args", result.base_args or "(none)")
    tbl.add_row("YOLO mode", f"[green]enabled[/green] ({result.yolo_args})" if result.yolo_enabled else "[dim]disabled[/dim]")
    tbl.add_row("Timeout (seconds)", str(result.timeout))
    tbl.add_row("Working directory", result.workdir or "(cwd)")
    tbl.add_row("Workspace retrieval", f"[green]enabled[/green]" if result.workspace_retrieval_enabled else "[dim]disabled[/dim]")
    console.print(tbl)

    if smoke_test:
        console.print()
        if result.smoke_test_passed:
            console.print("[green bold]✓ Smoke test passed[/green bold]")
            if result.smoke_test_output:
                console.print(f"  Response: {result.smoke_test_output[:200]}")
        else:
            console.print(f"[red bold]✗ Smoke test failed[/red bold]")
            if result.smoke_test_error:
                console.print(f"  Error: {result.smoke_test_error}")

    console.print()


# ---------------------------------------------------------------------------
# manager-os workspace-fetch-deal-docs
# ---------------------------------------------------------------------------

@app.command("workspace-fetch-deal-docs")
def workspace_fetch_deal_docs(
    target_date: Optional[str] = typer.Option(None, "--date", help="Date label for snapshot (YYYY-MM-DD)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without contacting Google Drive."),
    deal_id: Optional[str] = typer.Option(None, "--deal-id", help="Fetch docs for a single deal ID."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max deals to fetch docs for."),
    timeout: int = typer.Option(60, "--timeout", help="Timeout in seconds per Gemini CLI call."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Print the prompt that would be sent."),
    force: bool = typer.Option(False, "--force", help="Re-fetch even if results already exist."),
) -> None:
    """Retrieve INT SOW and Deal Sheet links from Google Drive for each deal.

    Searches Google Drive via Gemini CLI (read-only) for documents matching
    each deal's opportunity number and stores results in the deal_documents table.
    """
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.ingest.drive_deal_docs import (
        build_drive_search_prompt,
        fetch_deal_docs,
    )
    from rich import box as rich_box
    from rich.panel import Panel

    settings = get_settings()
    run_date = date.fromisoformat(target_date) if target_date else date.today()

    console.print(Panel.fit(
        "[bold]Manager OS — Workspace: Fetch Deal Docs[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))
    console.print(f"  [dim]Date:[/dim]     {run_date}")
    console.print(f"  [dim]Deal ID:[/dim]  {deal_id or '(all active deals)'}")
    console.print(f"  [dim]Limit:[/dim]    {limit or 'none'}")
    console.print(f"  [dim]Timeout:[/dim]  {timeout}s")
    if dry_run:
        console.print("  [yellow bold]DRY RUN — no CLI calls will be made[/yellow bold]")
    console.print()

    if print_prompt:
        # Print a sample prompt for inspection
        sample_prompt = build_drive_search_prompt(
            deal_id=deal_id or "<OPP-NUMBER>",
            deal_name="<Deal Name>",
            account="<Account>",
        )
        console.print("[bold]Sample prompt:[/bold]")
        console.print(sample_prompt)
        console.print()
        if dry_run:
            return

    conn = get_connection(settings.db_path)
    snapshot_dir = settings.gws_snapshot_dir

    result = fetch_deal_docs(
        conn,
        snapshot_dir=snapshot_dir,
        target_date=run_date,
        deal_id_filter=deal_id,
        limit=limit,
        bin_path=settings.gemini_cli_bin,
        model=settings.gemini_cli_model,
        timeout=timeout,
        yolo=settings.gemini_cli_yolo,
        workdir=settings.gemini_cli_workdir or "",
        dry_run=dry_run,
        force=force,
    )

    # Print results table
    tbl = Table(
        title=f"Deal docs — {run_date}{'  (dry run)' if dry_run else ''}",
        show_header=True,
        box=rich_box.SIMPLE,
    )
    tbl.add_column("Deal ID", style="cyan")
    tbl.add_column("Type", style="dim")
    tbl.add_column("Status")
    tbl.add_column("Title")
    tbl.add_column("URL")

    for r in result.results:
        status_str = (
            "[green]found[/green]" if r.search_status == "found"
            else ("[yellow]not found[/yellow]" if r.search_status == "not_found"
                  else f"[red]{r.search_status}[/red]")
        )
        tbl.add_row(
            r.deal_id[:20],
            r.document_type,
            status_str,
            r.title[:40] if r.title else "—",
            r.url[:50] if r.url else "—",
        )

    console.print(tbl)
    console.print(f"[dim]Fetched: {result.fetched}  Skipped: {result.skipped}  Failed: {result.failed}[/dim]")

    if result.errors:
        for err in result.errors[:5]:
            console.print(f"[yellow]⚠ {err}[/yellow]")


# ---------------------------------------------------------------------------
# manager-os people-audit
# ---------------------------------------------------------------------------

@app.command("people-audit")
def people_audit(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full alias map."),
) -> None:
    """Audit people config: canonical names, aliases, untracked, unconfigured.

    Reports:
    - Configured tracked people
    - People with track=false (excluded from dashboard)
    - Alias map (alias → canonical)
    - Duplicate candidates (names in DB that resolve to different canonical names)
    - Unconfigured names seen in notes/forecast/calendar but not in people.yaml
    """
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.people_normalization import run_people_audit
    from rich import box as rich_box
    from rich.panel import Panel

    settings = get_settings()
    conn = get_connection(settings.db_path)

    audit = run_people_audit(conn, settings)

    console.print(Panel.fit(
        "[bold]Manager OS — People Audit[/bold]",
        box=rich_box.ROUNDED,
        border_style="cyan",
    ))
    console.print()

    # Tracked
    console.print(f"[bold]✅ Tracked people ({len(audit.tracked)}):[/bold]")
    for name in audit.tracked:
        console.print(f"  • {name}")
    console.print()

    # Untracked
    if audit.untracked:
        console.print(f"[bold]⏸  Untracked (track=false) ({len(audit.untracked)}):[/bold]")
        for name in audit.untracked:
            console.print(f"  • {name}")
        console.print()

    # Duplicate candidates
    if audit.duplicate_candidates:
        console.print(f"[bold]🔄 Duplicate candidates ({len(audit.duplicate_candidates)}):[/bold]")
        for raw, canonical in sorted(audit.duplicate_candidates):
            console.print(f"  '{raw}' → '{canonical}'")
        console.print()
    else:
        console.print("[dim]✓ No duplicate candidates found.[/dim]")
        console.print()

    # Unconfigured
    if audit.unconfigured_in_db:
        console.print(f"[bold][yellow]⚠ Unconfigured names in DB ({len(audit.unconfigured_in_db)}):[/yellow][/bold]")
        for name in audit.unconfigured_in_db:
            console.print(f"  [yellow]• {name}[/yellow]")
        console.print("[dim]  These names appear in notes/forecast/signals but are not in people.yaml.[/dim]")
        console.print()
    else:
        console.print("[dim]✓ All DB people names are configured.[/dim]")
        console.print()

    # Alias map
    if verbose and audit.alias_map:
        console.print(f"[bold]Alias map ({len(audit.alias_map)} aliases):[/bold]")
        tbl = Table(show_header=True, box=rich_box.SIMPLE, show_edge=False)
        tbl.add_column("Alias", style="dim")
        tbl.add_column("→ Canonical")
        for alias, canonical in sorted(audit.alias_map.items()):
            tbl.add_row(alias, canonical)
        console.print(tbl)


# ---------------------------------------------------------------------------
# manager-os repair-feedback
# ---------------------------------------------------------------------------

@app.command("repair-feedback")
def repair_feedback(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview plan without writing."),
    yes: bool = typer.Option(False, "--yes", help="Required to perform writes."),
    archive_legacy: bool = typer.Option(
        False, "--archive-legacy",
        help="After backfill, rename legacy feedback table (requires --yes)."
    ),
) -> None:
    """Create feedback_events table and backfill from legacy feedback table."""
    from rich import box as rich_box
    from rich.panel import Panel
    from manager_os.config import get_settings
    from manager_os.db import get_connection, content_hash

    if not dry_run and not yes:
        console.print("[red]Use --dry-run to preview, or --yes to perform writes.[/red]")
        raise typer.Exit(1)

    settings = get_settings()
    console.print(Panel.fit("[bold]Manager OS — Repair Feedback[/bold]",
                  box=rich_box.ROUNDED, border_style="cyan"))
    console.print(f"  [dim]DB:[/dim]  {settings.db_path}")
    if dry_run:
        console.print("  [yellow bold]DRY RUN[/yellow bold]")
    console.print()

    conn = get_connection(settings.db_path)
    try:
        count_events = conn.execute("SELECT COUNT(*) FROM feedback_events").fetchone()[0]
        console.print(f"  [green]feedback_events exists[/green] ({count_events} events)")
    except Exception:
        console.print("  [yellow]feedback_events will be created[/yellow]")

    legacy_rows = []
    legacy_readable = False
    try:
        legacy_rows = conn.execute(
            "SELECT id, item_id, item_type, rating, reason, source_path, "
            "entity_name, signal_type, created_at FROM feedback"
        ).fetchall()
        legacy_readable = True
        console.print(f"  [dim]Legacy feedback:[/dim] {len(legacy_rows)} row(s)")
    except Exception:
        console.print("  [yellow]Legacy feedback unreadable — skip backfill[/yellow]")

    if dry_run:
        console.print(f"\n[yellow]Would backfill {len(legacy_rows) if legacy_readable else 0} rows[/yellow]")
        return

    inserted = 0
    if legacy_readable:
        for row in legacy_rows:
            old_id, item_id, item_type, rating, reason, source_path, entity_name, signal_type, created_at = row
            from datetime import datetime as _dt2
            ts_str = str(created_at) if created_at else _dt2.utcnow().isoformat()
            new_id = content_hash(f"backfill::{old_id}::{item_id}::{rating}::{ts_str}")
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO feedback_events
                       (id, item_id, item_type, rating, reason, source_path,
                        entity_name, signal_type, created_at, created_by, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [new_id, item_id, item_type or "unknown", rating or "",
                     reason, source_path, entity_name, signal_type, created_at,
                     "repair-feedback-backfill", None],
                )
                inserted += 1
            except Exception:
                pass

    console.print(f"[green]✓ Backfilled: {inserted}[/green]")
    conn.close()


# ---------------------------------------------------------------------------
# manager-os feedback-summary / feedback-events / feedback-candidates
# ---------------------------------------------------------------------------

@app.command("feedback-summary")
def feedback_summary_cmd() -> None:
    """Print aggregate feedback statistics from feedback_events."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.feedback import get_feedback_summary

    settings = get_settings()
    conn = get_connection(settings.db_path)
    summary = get_feedback_summary(conn)

    console.print(f"[bold]Feedback Summary[/bold] ({summary['total']} total events)")
    console.print()
    for rating, count in sorted(summary["counts_by_rating"].items()):
        console.print(f"  {rating:20s} {count}")

    if summary.get("top_noisy_sources"):
        console.print("\n[bold]Top Noisy Sources:[/bold]")
        for path, n in summary["top_noisy_sources"][:5]:
            console.print(f"  {path} ({n}x)")

    if summary.get("top_wrong_types"):
        console.print("\n[bold]Top Wrong Signal Types:[/bold]")
        for t, n in summary["top_wrong_types"][:5]:
            console.print(f"  {t} ({n}x)")

    conn.close()


@app.command("feedback-events")
def feedback_events_cmd(
    limit: int = typer.Option(20, "--limit", help="Number of recent events to show."),
) -> None:
    """Print recent feedback events."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.feedback import list_feedback

    settings = get_settings()
    conn = get_connection(settings.db_path)
    events = list_feedback(conn, limit=limit)

    if not events:
        console.print("[dim]No feedback events.[/dim]")
        conn.close()
        return

    tbl = Table(title=f"Recent Feedback Events (last {len(events)})", show_header=True)
    tbl.add_column("Item", style="cyan")
    tbl.add_column("Rating")
    tbl.add_column("Entity")
    tbl.add_column("Source")
    tbl.add_column("When")

    for e in events:
        tbl.add_row(
            e["item_id"][:30],
            e["rating"],
            e.get("entity_name", "") or "—",
            (e.get("source_path", "") or "")[:40],
            str(e.get("created_at", ""))[:19],
        )
    console.print(tbl)
    conn.close()


@app.command("feedback-candidates")
def feedback_candidates_cmd() -> None:
    """Print open feedback learning candidates."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.feedback_policy import list_learning_candidates

    settings = get_settings()
    conn = get_connection(settings.db_path)
    candidates = list_learning_candidates(conn, status="open")

    if not candidates:
        console.print("[dim]No open learning candidates.[/dim]")
        conn.close()
        return

    tbl = Table(title="Open Learning Candidates", show_header=True)
    tbl.add_column("Rating", style="red")
    tbl.add_column("Pattern")
    tbl.add_column("Source/Entity")
    tbl.add_column("Count", justify="right")
    tbl.add_column("Suggested Action")

    for c in candidates:
        tbl.add_row(
            c["rating"],
            c["pattern_type"],
            c.get("source_path", "") or c.get("entity_name", "") or "—",
            str(c["event_count"]),
            c.get("suggested_action", "—"),
        )
    console.print(tbl)
    conn.close()


if __name__ == "__main__":
    app()

