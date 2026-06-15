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

    if ro_conn is not None:
        ro_conn.close()

    console.print(tbl)
    console.print()
    console.print(
        "[yellow bold]⚠  Dry run — nothing was written to the database.[/yellow bold]"
    )
    console.print()


def _do_dry_run_extract(settings, run_date: "date", mode: str) -> None:
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
        help="Source to ingest: all | obsidian | forecast | deals | summary",
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

    valid_sources = {"all", "obsidian", "forecast", "deals", "summary", "gws"}
    if source not in valid_sources:
        console.print(f"[red]Unknown source '{source}'. Must be one of: {', '.join(sorted(valid_sources))}[/red]")
        raise typer.Exit(1)

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
    table.add_column("Skipped", justify="right", style="yellow")
    table.add_column("Failed", justify="right", style="red")

    run_obsidian = source in ("all", "obsidian")
    run_forecast = source in ("all", "forecast")
    run_deals = source in ("all", "deals")
    run_summary = source in ("all", "summary")
    run_gws = source in ("all", "gws")

    had_error = False
    _source_results: list[tuple[str, object]] = []

    if run_obsidian:
        if not settings.vault_path:
            console.print("[red]MANAGER_OS_VAULT_PATH is not set. Set it in your .env file.[/red]")
            had_error = True
        else:
            try:
                r = ingest_vault(settings.vault_path, conn, force=force)
                table.add_row("obsidian", str(r.ingested), str(r.skipped), str(r.failed))
                _source_results.append(("obsidian", r))
                if r.failed:
                    had_error = True
            except FileNotFoundError as exc:
                console.print(f"[red]Vault not found: {exc}[/red]")
                had_error = True

    if run_forecast:
        try:
            r = ingest_forecast(settings.forecast_csv, conn, source_priority=sp, force=force)
            table.add_row("forecast", str(r.ingested), str(r.skipped), str(r.failed))
            _source_results.append(("forecast", r))
            if r.failed:
                had_error = True
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[red]Forecast CSV error: {exc}[/red]")
            had_error = True

    if run_deals:
        try:
            r = ingest_deals(settings.deals_csv, conn, source_priority=sp, force=force)
            table.add_row("deals", str(r.ingested), str(r.skipped), str(r.failed))
            _source_results.append(("deals", r))
            if r.failed:
                had_error = True
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[red]Deals CSV error: {exc}[/red]")
            had_error = True

    if run_summary:
        r = ingest_summary(settings.workspace_summary_dir, target_date, conn, force=force)
        table.add_row("summary", str(r.ingested), str(r.skipped), str(r.failed))
        _source_results.append(("summary", r))

    if run_gws:
        from manager_os.ingest.gws_client import ingest_gws_snapshots
        r = ingest_gws_snapshots(settings.gws_snapshot_dir, conn,
                                 target_date=target_date, force=force)
        table.add_row("gws", str(r.ingested), str(r.skipped), str(r.failed))
        _source_results.append(("gws", r))
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
) -> None:
    """Extract signals and action items from ingested documents."""
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

    if dry_run:
        _do_dry_run_extract(settings, run_date, mode)
        return

    conn = get_connection(settings.db_path)

    # Check that documents exist
    doc_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    if doc_count == 0:
        console.print("[red]No notes found in the database. Run 'manager-os ingest' first.[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Extraction results — {run_date}", show_header=True)
    table.add_column("Step", style="cyan")
    table.add_column("Written", justify="right", style="green")
    table.add_column("Skipped", justify="right", style="yellow")
    table.add_column("Failed", justify="right", style="red")

    _step_results: list[tuple[str, object]] = []

    if mode in ("rules", "both"):
        result = run_rule_extraction(conn, run_date=run_date)
        table.add_row("signals (rules)", str(result.written), str(result.skipped), str(result.failed))
        _step_results.append(("signals (rules)", result))

    if mode in ("llm", "both"):
        from manager_os.extract.llm_signals import run_llm_extraction, LLMExtractionUnavailable
        try:
            llm_result = run_llm_extraction(conn, run_date=run_date)
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
    console.print(f"  {len(b.signal_ids)} signal(s) included")


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
    ing_table.add_column("Skipped", justify="right", style="yellow")
    ing_table.add_column("Failed", justify="right", style="red")

    if settings.vault_path:
        try:
            r = ingest_vault(settings.vault_path, conn, force=True)
            ing_table.add_row("obsidian", str(r.ingested), str(r.skipped), str(r.failed))
        except FileNotFoundError as exc:
            console.print(f"[yellow]Vault not found, skipping: {exc}[/yellow]")

    try:
        r = ingest_forecast(settings.forecast_csv, conn, source_priority=sp, force=True)
        ing_table.add_row("forecast", str(r.ingested), str(r.skipped), str(r.failed))
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[yellow]Forecast CSV not found, skipping: {exc}[/yellow]")

    try:
        r = ingest_deals(settings.deals_csv, conn, source_priority=sp, force=True)
        ing_table.add_row("deals", str(r.ingested), str(r.skipped), str(r.failed))
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[yellow]Deals CSV not found, skipping: {exc}[/yellow]")

    r = ingest_summary(settings.workspace_summary_dir, target_date, conn, force=True)
    ing_table.add_row("summary", str(r.ingested), str(r.skipped), str(r.failed))

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


def _is_sample_config(settings) -> bool:
    """Return True when the configured paths look like sample/fixture data."""
    if settings.vault_path:
        vp = Path(settings.vault_path)
        if _within_repo(vp) or _path_has_safe_keyword(vp):
            return True
    db_p = Path(settings.db_path)
    if _within_repo(db_p) or _path_has_safe_keyword(db_p):
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
    sample_warning = _is_sample_config(settings)

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
        console.print()
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
        }
        for issue in result.issues:
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
        "zero_allocation", "missing_date",
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
    _REQ = ["account", "deal_name"]
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
    console.print(req_tbl)
    console.print()

    # Optional fields coverage
    _OPT_DISPLAY = [
        ("stage", "stage"),
        ("close_date", "close date"),
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

        for issue in result.issues:
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

    error_types = {"malformed_close_date", "malformed_probability"}
    warn_types = {
        "close_date_soon", "missing_close_date", "missing_sow", "missing_loe",
        "no_owner", "unknown_client", "high_value_no_staffing",
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


if __name__ == "__main__":
    app()

