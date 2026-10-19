"""
Layer rules for the codebase (see docs/ARCHITECTURE.md).

A folder may import from its own layer or any layer BELOW it, never above.
Portable packages (packages/*) may not import any FlowOne application folder.
Business-rule folders never read configuration directly.

If this test fails, move the code to the right layer or pass the dependency
in from above (constructor argument) instead of importing it.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

# 1 = top (entry points) ... 5 = bottom (shared basics)
LAYERS = {
    "api": 1, "tools": 1,
    "agent": 2, "adapters": 2,
    "nlu": 3, "semantic": 3, "planning": 3, "sql_compiler": 3, "sql_validator": 3,
    "sql_guardrails": 3, "result_analysis": 3, "answering": 3, "golden": 3,
    "ai": 4, "database": 4, "execution": 4, "ontology": 4,
    "observability": 5, "config": 5,
}

BUSINESS_RULES = {name for name, layer in LAYERS.items() if layer == 3}

# Known upward imports, kept visible on purpose. Do not add to this list
# without recording why in docs/ARCHITECTURE.md.
KNOWN_EXCEPTIONS = {
    # The executor accepts the compiled, validated and approved query
    # objects produced one layer above it.
    ("execution", "sql_compiler"),
    ("execution", "sql_validator"),
    ("execution", "sql_guardrails"),
}


def imports_of(folder: Path):
    for path in folder.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                yield path.relative_to(ROOT), name.split(".")[0]


def test_every_application_folder_has_a_layer():
    folders = {p.name for p in ROOT.iterdir()
               if p.is_dir() and (p / "__init__.py").exists() and p.name != "tests"}
    assert folders <= set(LAYERS), f"Add these folders to LAYERS: {sorted(folders - set(LAYERS))}"


@pytest.mark.parametrize("folder", sorted(n for n in LAYERS if n != "config"))
def test_imports_only_point_down(folder):
    upward = sorted({
        f"{path} imports {target}"
        for path, target in imports_of(ROOT / folder)
        if target in LAYERS and target != folder
        and LAYERS[target] < LAYERS[folder]
        and (folder, target) not in KNOWN_EXCEPTIONS
    })
    assert not upward, "Upward import(s):\n" + "\n".join(upward)


@pytest.mark.parametrize("folder", sorted(BUSINESS_RULES))
def test_business_rules_never_read_configuration(folder):
    readers = sorted({str(path) for path, target in imports_of(ROOT / folder) if target == "config"})
    assert not readers, f"Pass settings in from the composition layer instead: {readers}"


@pytest.mark.parametrize("package", sorted(p.name for p in (ROOT / "packages").iterdir() if p.is_dir()))
def test_portable_packages_import_no_application_code(package):
    used = sorted({f"{path} imports {target}"
                   for path, target in imports_of(ROOT / "packages" / package / "src")
                   if target in LAYERS})
    assert not used, "Portable package depends on the application:\n" + "\n".join(used)
