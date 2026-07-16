from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from agent_doc_bench.doc_source import get_variant, list_variants

STUB_MARKER = "**Stub.**"


@dataclass
class DocIssue:
    experiment: str
    api: str
    value: str
    kind: str  # "missing" | "empty_non_none" | "stub"
    path: Path
    detail: str


def validate_docs(
    experiments_dir: Path = Path("experiments"),
    docs_base: Path = Path("docs_library"),
    stub_threshold_chars: int = 200,
) -> list[DocIssue]:
    """Check every experiment's documentation-variant values resolve to a
    real, non-placeholder doc file. Never raises on one bad file — collects
    all issues, mirroring the failure-isolation pattern in
    scorers/base.py's run_scorer().
    """
    issues: list[DocIssue] = []

    for exp_path in sorted(Path(experiments_dir).glob("*.yaml")):
        with open(exp_path) as f:
            data = yaml.safe_load(f) or {}

        variable = data.get("variable") or {}
        if variable.get("name") != "documentation":
            continue

        api = data["task_suite"]
        experiment = data.get("name", exp_path.stem)
        existing = set(list_variants(api, docs_base))

        for value in variable.get("values", []):
            path = Path(docs_base) / api / f"{value}.md"

            if value not in existing:
                issues.append(
                    DocIssue(
                        experiment=experiment,
                        api=api,
                        value=value,
                        kind="missing",
                        path=path,
                        detail=f"{path} does not exist — a run would silently use no documentation for {value!r}",
                    )
                )
                continue

            content = get_variant(api, value, docs_base)
            stripped = content.strip()

            if not stripped:
                if value != "none":
                    issues.append(
                        DocIssue(
                            experiment=experiment,
                            api=api,
                            value=value,
                            kind="empty_non_none",
                            path=path,
                            detail=f"{path} is empty — only 'none' may be the empty baseline",
                        )
                    )
                continue

            if STUB_MARKER in content or len(stripped) < stub_threshold_chars:
                issues.append(
                    DocIssue(
                        experiment=experiment,
                        api=api,
                        value=value,
                        kind="stub",
                        path=path,
                        detail=f"{path} looks like a stub ({len(stripped)} chars)",
                    )
                )

    return issues
