"""Manager OS — CLI entry point."""

from __future__ import annotations

import sys
from datetime import date
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

    if run_obsidian:
        if not settings.vault_path:
            console.print("[red]MANAGER_OS_VAULT_PATH is not set. Set it in your .env file.[/red]")
            had_error = True
        else:
            try:
                r = ingest_vault(settings.vault_path, conn, force=force)
                table.add_row("obsidian", str(r.ingested), str(r.skipped), str(r.failed))
                if r.failed:
                    had_error = True
            except FileNotFoundError as exc:
                console.print(f"[red]Vault not found: {exc}[/red]")
                had_error = True

    if run_forecast:
        try:
            r = ingest_forecast(settings.forecast_csv, conn, source_priority=sp, force=force)
            table.add_row("forecast", str(r.ingested), str(r.skipped), str(r.failed))
            if r.failed:
                had_error = True
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[red]Forecast CSV error: {exc}[/red]")
            had_error = True

    if run_deals:
        try:
            r = ingest_deals(settings.deals_csv, conn, source_priority=sp, force=force)
            table.add_row("deals", str(r.ingested), str(r.skipped), str(r.failed))
            if r.failed:
                had_error = True
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[red]Deals CSV error: {exc}[/red]")
            had_error = True

    if run_summary:
        r = ingest_summary(settings.workspace_summary_dir, target_date, conn, force=force)
        table.add_row("summary", str(r.ingested), str(r.skipped), str(r.failed))

    if run_gws:
        from manager_os.ingest.gws_client import ingest_gws_snapshots
        r = ingest_gws_snapshots(settings.gws_snapshot_dir, conn,
                                 target_date=target_date, force=force)
        table.add_row("gws", str(r.ingested), str(r.skipped), str(r.failed))
        if r.failed:
            had_error = True

    console.print(table)
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

    if mode in ("rules", "both"):
        result = run_rule_extraction(conn, run_date=run_date)
        table.add_row("signals (rules)", str(result.written), str(result.skipped), str(result.failed))

    if mode in ("llm", "both"):
        from manager_os.extract.llm_signals import run_llm_extraction, LLMExtractionUnavailable
        try:
            llm_result = run_llm_extraction(conn, run_date=run_date)
            table.add_row("signals (llm)", str(llm_result.written), str(llm_result.skipped), str(llm_result.failed))
        except LLMExtractionUnavailable as exc:
            console.print(f"[yellow]LLM extraction skipped: {exc}[/yellow]")

    # Action items always run
    ai_result = extract_action_items_from_all_notes(conn)
    table.add_row("action items", str(ai_result.written), str(ai_result.skipped), str(ai_result.failed))

    # Decisions always run
    from manager_os.extract.decisions import extract_decisions_from_all_notes
    dec_result = extract_decisions_from_all_notes(conn)
    table.add_row("decisions", str(dec_result.written), str(dec_result.skipped), str(dec_result.failed))

    console.print(table)


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


if __name__ == "__main__":
    app()
