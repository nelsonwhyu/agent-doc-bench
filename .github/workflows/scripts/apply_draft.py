#!/usr/bin/env python3
"""Applies a PM's documentation draft (from the evaluate-doc-draft
repository_dispatch event) to a local-only working copy: writes the draft
content to docs_library/<api>/<value>.md and, if <value> isn't already one
of the experiment's swept values, adds it there too. Both changes live only
on this runner's disk and are never committed or pushed — see
IMPLEMENTATION_PLAN.md's Layer 2 "evaluate_doc_draft flow".

DRAFT_CONTENT is untrusted, PM-authored text. It's read only from the
environment (never a shell command line) and only ever written to a file,
never interpreted. DRAFT_API/DRAFT_VALUE/DRAFT_EXPERIMENT are validated as
plain identifiers before being used as path components, since they cross
the same untrusted boundary (the public repository_dispatch API).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]+$")


def require_identifier(env_var: str) -> str:
    value = os.environ[env_var]
    if not IDENTIFIER.fullmatch(value):
        sys.exit(
            f"{env_var}={value!r} isn't a safe identifier (expected [A-Za-z0-9_.-]+) "
            "— refusing to use it as a path component."
        )
    return value


def main() -> None:
    api = require_identifier("DRAFT_API")
    value = require_identifier("DRAFT_VALUE")
    experiment = require_identifier("DRAFT_EXPERIMENT")
    content = os.environ["DRAFT_CONTENT"]

    doc_path = Path("docs_library") / api / f"{value}.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(content)

    exp_path = Path("experiments") / f"{experiment}.yaml"
    if not exp_path.exists():
        sys.exit(f"{exp_path} does not exist.")

    data = yaml.safe_load(exp_path.read_text()) or {}
    variable = data.get("variable") or {}
    if variable.get("name") != "documentation":
        sys.exit(f"{exp_path} does not sweep a 'documentation' variable — evaluate_doc_draft doesn't apply to it.")

    values = variable.setdefault("values", [])
    if value not in values:
        values.append(value)
        exp_path.write_text(yaml.safe_dump(data, sort_keys=False))


if __name__ == "__main__":
    main()
