from __future__ import annotations

from pathlib import Path

from agent_doc_bench.tasks.task_registry import load_suite


def build_doc_requirements(api: str, base_dir: str | Path = "task_suites") -> str:
    """Plain-language Markdown checklist of what a documentation writer might
    want to keep in mind for docs_library/<api>/*.md, generated on the fly
    from task_suites/<api>/*.yaml — no second source of truth, so it can
    never drift from the patterns/rubrics actually used to score a run.

    This is reference material, not a spec: scorers grade the code a coding
    agent produces with the doc's help, never the doc's wording directly, so
    a writer is free to cover this ground however reads best — there's no
    required phrasing or structure to match.
    """
    tasks = load_suite(api, base_dir)

    lines = [
        f"# Documentation guidance for `{api}`",
        "",
        f"Derived from `task_suites/{api}/*.yaml` — a reference for what tends to help an agent "
        "get each benchmark task right, not a template to follow. Scorers grade the code a coding "
        "agent writes with a doc's help, never the doc's wording itself, so write however reads "
        "best for a real developer — this is just a sense check of the ground worth covering.",
    ]

    for task in tasks:
        lines += ["", f"## {task.id}", "", f"> {task.instruction.strip()}"]

        if task.expected_patterns:
            lines += ["", "**Tends to help the agent show it knows how to:**"]
            lines += [f"- {p.label}" for p in task.expected_patterns]

        if task.anti_patterns:
            lines += ["", "**Common ways agents go wrong here:**"]
            lines += [f"- {p.label}" for p in task.anti_patterns]

        if task.llm_judge_rubric.strip():
            lines += ["", "**What a strong generated answer looks like:**", "", task.llm_judge_rubric.strip()]

    return "\n".join(lines) + "\n"
