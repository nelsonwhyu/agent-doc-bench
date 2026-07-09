from __future__ import annotations

import json
from dataclasses import asdict

from rich.table import Table

from agent_doc_bench.reporting.results_fetcher import ExperimentResult

# Scorers whose score is a strict 0.0/1.0 pass-fail (see execution_scorer.py,
# syntax_scorer.py) render as check/cross rather than a decimal — the other
# scorers (pattern/llm_judge/static_analysis) are 0-1 floats worth seeing at
# more than pass/fail resolution.
PASS_FAIL_SCORERS = {"syntax_score", "execution_score"}


def _fmt_score(key: str, value: float | bool | None) -> str:
    if value is None:
        return "–"
    if key in PASS_FAIL_SCORERS:
        return "✓" if value else "✗"
    return f"{float(value):.2f}"


def render_table(result: ExperimentResult) -> tuple[Table, Table]:
    summary = Table(title=f"{result.config_name} — summary (mean per {result.variable_name})")
    summary.add_column(result.variable_name, style="cyan")
    for key in result.scorer_keys:
        summary.add_column(key)
    summary.add_column("turns")
    summary.add_column("tokens")

    for variant in result.variants:
        row = [variant.variable_value]
        row += [_fmt_score(key, variant.mean_score(key)) for key in result.scorer_keys]
        row.append(_fmt_score("metric_n_turns", variant.mean_metric("metric_n_turns")))
        row.append(_fmt_score("metric_n_total_tokens", variant.mean_metric("metric_n_total_tokens")))
        summary.add_row(*row)

    detail = Table(title=f"{result.config_name} — per task")
    detail.add_column(result.variable_name, style="cyan")
    detail.add_column("task")
    for key in result.scorer_keys:
        detail.add_column(key)

    for variant in result.variants:
        for task in variant.task_results:
            row = [variant.variable_value, task.task_id]
            row += [_fmt_score(key, task.scores.get(key)) for key in result.scorer_keys]
            detail.add_row(*row)

    return summary, detail


def render_json(result: ExperimentResult) -> str:
    return json.dumps(asdict(result), default=str, indent=2)


def render_markdown(result: ExperimentResult) -> str:
    lines = [f"# {result.config_name} — ablation results\n"]

    lines.append(f"## Summary (mean per {result.variable_name})\n")
    header = [result.variable_name, *result.scorer_keys, "turns", "tokens"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for variant in result.variants:
        row = [variant.variable_value]
        row += [_fmt_score(key, variant.mean_score(key)) for key in result.scorer_keys]
        row.append(_fmt_score("metric_n_turns", variant.mean_metric("metric_n_turns")))
        row.append(_fmt_score("metric_n_total_tokens", variant.mean_metric("metric_n_total_tokens")))
        lines.append("| " + " | ".join(row) + " |")

    if result.missing_values:
        lines.append(f"\n_No experiment found for {result.variable_name} = {result.missing_values}_")

    for variant in result.variants:
        lines.append(f"\n## {result.variable_name} = {variant.variable_value}\n")
        for task in variant.task_results:
            lines.append(f"### {task.task_id}\n")
            score_line = ", ".join(f"{k}={_fmt_score(k, task.scores.get(k))}" for k in result.scorer_keys)
            lines.append(f"Scores: {score_line}\n")
            for key, comment in task.comments.items():
                if comment:
                    lines.append(f"- **{key}**: {comment}")
            if task.error:
                lines.append(f"\nError: `{task.error}`")
            lines.append("\n```python\n" + (task.generated_code or "(no code produced)") + "\n```")

    return "\n".join(lines)
