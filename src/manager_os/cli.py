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
) -> None:
    """Generate a daily markdown brief."""
    from manager_os.config import get_settings
    from manager_os.db import get_connection
    from manager_os.build.daily_brief import generate_daily_brief, write_brief_to_file

    settings = get_settings()
    target_date = date.fromisoformat(brief_date) if brief_date else date.today()
    conn = get_connection(settings.db_path)

    b = generate_daily_brief(conn, target_date=target_date)
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


if __name__ == "__main__":
    app()
