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
    experiment: str = typer.Option(..., help="Experiment name prefix to fetch from LangSmith"),
) -> None:
    """Print a summary table of the last experiment from LangSmith."""
    from langsmith import Client

    client = Client()
    console.print(f"Fetching results for: [bold]{experiment}[/bold]\n")

    # Each ablation run creates its own LangSmith experiment (a TracerSession
    # named "{experiment}__{variable}_{value}-{suffix}"), not a run inside a
    # shared project — so experiments are looked up via list_projects.
    sessions = list(client.list_projects(name_contains=experiment))
    if not sessions:
        console.print("[red]No experiments found.[/red]")
        raise typer.Exit(1)

    for session in sessions:
        console.print(f"  {session.name}  —  started {session.start_time}")


if __name__ == "__main__":
    app()
