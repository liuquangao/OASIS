#!/usr/bin/env python3
"""Regenerate constraints.txt from the currently installed environment.

Walks the dependency closure declared in pyproject.toml and pins every package
pip would install, so a later checkout resolves the same versions. Packages that
happen to be in the virtualenv but are not reachable from the declared
dependencies (playwright, for example) are deliberately left out.

Run from the repository root, inside the environment you want to capture:

    python scripts/write_constraints.py
"""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata as md
import sys
import tomllib
from pathlib import Path

from packaging.markers import UndefinedEnvironmentName
from packaging.requirements import Requirement

PROJECT_ROOT = Path(__file__).resolve().parent.parent

HEADER = """\
# Pinned versions for a reproducible HydroMind environment.
#
# Install with:
#     pip install -e ".[dev]" -c constraints.txt
#
# This is a constraints file, not a requirements file: it pins the version of
# anything pip decides to install, but never installs a package by itself. That
# keeps it valid on Windows, macOS and Linux even though it was resolved on
# {platform}/CPython {python} -- a platform-only transitive dependency (colorama,
# for example) still resolves normally, it simply is not pinned here.
#
# Regenerate with: python scripts/write_constraints.py
#
# Resolved: {date} - CPython {python}
"""


def closure(roots: list[str]) -> list[tuple[str, str]]:
    """Pin every installed distribution reachable from the declared dependencies."""

    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    missing: list[str] = []

    def visit(spec: str, parent_extras: frozenset[str]) -> None:
        requirement = Requirement(spec)
        if requirement.marker is not None:
            contexts = parent_extras or frozenset({""})
            try:
                if not any(requirement.marker.evaluate({"extra": extra}) for extra in contexts):
                    return
            except UndefinedEnvironmentName:
                return
        key = requirement.name.lower().replace("_", "-")
        if key in seen:
            return
        try:
            version = md.version(requirement.name)
        except md.PackageNotFoundError:
            missing.append(requirement.name)
            return
        seen.add(key)
        found.append((requirement.name, version))
        for dependency in md.requires(requirement.name) or []:
            visit(dependency, frozenset(requirement.extras))

    for spec in roots:
        visit(spec, frozenset())

    if missing:
        raise SystemExit(
            "Not installed, so their versions cannot be pinned: "
            + ", ".join(sorted(missing))
            + "\nInstall the project first: pip install -e '.[dev]'"
        )
    return sorted(found, key=lambda item: item[0].lower())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if constraints.txt is out of date instead of rewriting it.",
    )
    args = parser.parse_args()

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    roots = list(project["dependencies"]) + list(
        project.get("optional-dependencies", {}).get("dev", [])
    )

    header = HEADER.format(
        platform=sys.platform,
        python=".".join(str(part) for part in sys.version_info[:2]),
        date=datetime.date.today().isoformat(),
    )
    body = "\n".join(f"{name}=={version}" for name, version in closure(roots))
    content = f"{header}{body}\n"

    target = PROJECT_ROOT / "constraints.txt"
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        # Compare pins only; the header records when and where it was resolved.
        def pins(text: str) -> list[str]:
            return [line for line in text.splitlines() if line and not line.startswith("#")]

        if pins(current) != pins(content):
            print(f"{target} is out of date. Regenerate with: python {Path(__file__).name}")
            return 1
        print(f"{target} matches the installed environment.")
        return 0

    target.write_text(content, encoding="utf-8")
    print(f"Wrote {target} with {len(pins_of(body))} pinned packages.")
    return 0


def pins_of(body: str) -> list[str]:
    return [line for line in body.splitlines() if line]


if __name__ == "__main__":
    raise SystemExit(main())
