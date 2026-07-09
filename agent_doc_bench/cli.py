from __future__ import annotations

from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

app = typer.Typer(help="agent-doc-bench: evaluate how documentation helps AI coding agents.")
console = Console()


@app.command()
def run(
    experiment: Path = typer.Argument(..., help="Path to experiment YAML config"),
    docs_base: Path = typer.Option(Path("docs_library"), help="Root directory for docs variants"),
) -> None:
    """Run an ablation experiment and push results to LangSmith."""
    from agent_doc_bench.config import ExperimentConfig
    from agent_doc_bench.runner import run_experiment

    config = ExperimentConfig.from_yaml(experiment)
    console.print(f"[bold]Running:[/bold] {config.name}")
    console.print(f"  Varying: [cyan]{config.variable.name}[/cyan] over {config.variable.values}")
    console.print(f"  Tasks:   {config.task_suite}")
    console.print(f"  Scorers: {config.scorers}\n")

    run_experiment(config, docs_base=docs_base)
    console.print("\n[green]Done.[/green] View results in LangSmith.")


@app.command()
def report(
    experiment: str = typer.Argument(
        ..., help="Path to the experiment YAML config (preferred, e.g. experiments/doc_ablation.yaml), "
        "or a bare experiment name for a legacy name-only lookup"
    ),
    format: str = typer.Option("table", "--format", help="Output format: table, json, or markdown"),
    output: Path | None = typer.Option(None, "--output", help="Write json/markdown to this file instead of stdout"),
) -> None:
    """Print a scored results table for an experiment, pulled from LangSmith."""
    path = Path(experiment)
    if not (path.exists() and path.suffix in (".yaml", ".yml")):
        _report_legacy(experiment)
        return

    from agent_doc_bench.config import ExperimentConfig
    from agent_doc_bench.reporting.report_formatters import render_json, render_markdown, render_table
    from agent_doc_bench.reporting.results_fetcher import fetch_experiment_results

    config = ExperimentConfig.from_yaml(path)
    console.print(f"Fetching results for: [bold]{config.name}[/bold]\n")
    result = fetch_experiment_results(config)

    if result.missing_values:
        console.print(f"[yellow]No experiment found for {config.variable.name} = {result.missing_values}[/yellow]")
    if not result.variants:
        console.print("[red]No experiments found.[/red]")
        raise typer.Exit(1)

    if format == "table":
        summary, detail = render_table(result)
        console.print(summary)
        console.print()
        console.print(detail)
    elif format in ("json", "markdown"):
        text = render_json(result) if format == "json" else render_markdown(result)
        if output:
            output.write_text(text)
            console.print(f"[green]Wrote {format} report to {output}[/green]")
        else:
            console.print(text)
    else:
        console.print(f"[red]Unknown format: {format!r} (expected table, json, or markdown)[/red]")
        raise typer.Exit(1)


def _report_legacy(experiment: str) -> None:
    """Fallback when `experiment` isn't a YAML config path: list matching
    LangSmith experiment names only, same as this command's old behavior —
    no per-task score detail is available without a config to resolve the
    dataset/scorer keys against.
    """
    from langsmith import Client

    client = Client()
    console.print(f"Fetching results for: [bold]{experiment}[/bold]\n")

    sessions = list(client.list_projects(name_contains=experiment))
    if not sessions:
        console.print("[red]No experiments found.[/red]")
        raise typer.Exit(1)

    for session in sessions:
        console.print(f"  {session.name}  —  started {session.start_time}")
    console.print("\n[dim]Pass the experiment's YAML config path instead of a bare name for full score detail.[/dim]")


if __name__ == "__main__":
    app()
