#!/bin/bash
set -euo pipefail
WORKTREE=/home/alex/Projects/worktrees/wave1-issue407
ISSUE=407
BRANCH=fix/issue-407-ifcopenshell-graceful
cd $WORKTREE

git checkout -b $BRANCH 2>&1
git status --short

# The crash happens because ifcopenshell has C extensions that can fail
# at import time (missing .so, broken binary, etc.)
# Fix: wrap ALL ifcopenshell imports in try/except with PipelineDependencyError

# --- Fix 1: Create pipeline exceptions module ---
cat > pipeline_exceptions.py << 'EOF'
"""Pipeline-specific exception hierarchy."""


class PipelineError(Exception):
    """Base exception for all pipeline errors."""
    pass


class PipelineDependencyError(PipelineError):
    """Raised when an optional dependency (e.g. ifcopenshell) is unavailable or broken."""
    pass


class PipelineValidationError(PipelineError):
    """Raised when validate.py finds a conservation-law violation."""
    pass
EOF

# --- Fix 2: ifc_import.py — wrap _ensure_ifc ---
sed -i '51,53s/.*/    try:\n        import ifcopenshell  # noqa: F401\n    except OSError as e:\n        raise PipelineDependencyError(\n            "ifcopenshell is installed but failed to import (likely a broken binary or "\n            "missing system library). Install with: pip install ifcopenshell --force-reinstall"\n        ) from e/' ifc_import.py 2>&1 || true

# Check if the sed worked; if not, do it manually
if ! grep -q "PipelineDependencyError" ifc_import.py; then
    python3 << 'PYEOF'
with open("ifc_import.py", "r") as f:
    content = f.read()

old = '''    if importlib.util.find_spec("ifcopenshell") is None:
        raise RuntimeError("IfcOpenShell is not installed; install with `pip install ifcopenshell`")
    import ifcopenshell  # noqa: F401'''

new = '''    if importlib.util.find_spec("ifcopenshell") is None:
        raise PipelineDependencyError(
            "IfcOpenShell is not installed; install with `pip install ifcopenshell`"
        )
    try:
        import ifcopenshell  # noqa: F401
    except OSError as e:
        raise PipelineDependencyError(
            "ifcopenshell is installed but failed to import (broken binary or missing "
            "system library). Reinstall with: pip install ifcopenshell --force-reinstall"
        ) from e'''

content = content.replace(old, new)
with open("ifc_import.py", "w") as f:
    f.write(content)
print("Patched ifc_import.py")
PYEOF
fi

# --- Fix 3: bem_ifc4.py — wrap _ensure_ifc similarly ---
python3 << 'PYEOF'
with open("bem_ifc4.py", "r") as f:
    content = f.read()

old = '''    if importlib.util.find_spec("ifcopenshell") is None:
        raise RuntimeError("IfcOpenShell is not installed; install with `pip install ifcopenshell`")
    import ifcopenshell  # noqa: F401'''

new = '''    if importlib.util.find_spec("ifcopenshell") is None:
        raise PipelineDependencyError(
            "IfcOpenShell is not installed; install with `pip install ifcopenshell`"
        )
    try:
        import ifcopenshell  # noqa: F401
    except OSError as e:
        raise PipelineDependencyError(
            "ifcopenshell is installed but failed to import (broken binary or missing "
            "system library). Reinstall with: pip install ifcopenshell --force-reinstall"
        ) from e'''

content = content.replace(old, new)
with open("bem_ifc4.py", "w") as f:
    f.write(content)
print("Patched bem_ifc4.py")
PYEOF

# --- Fix 4: validate/gbxml.py — wrap the ifcopenshell import ---
python3 << 'PYEOF'
with open("validate/gbxml.py", "r") as f:
    content = f.read()

old = "        import ifcopenshell\n\n        f = ifcopenshell.open(str(path))"
new = """        try:
            import ifcopenshell
        except OSError as e:
            raise PipelineDependencyError(
                "ifcopenshell is installed but failed to import. "
                "Reinstall with: pip install ifcopenshell --force-reinstall"
            ) from e

        f = ifcopenshell.open(str(path))"""

content = content.replace(old, new)
with open("validate/gbxml.py", "w") as f:
    f.write(content)
print("Patched validate/gbxml.py")
PYEOF

# --- Fix 5: Update cli.py to handle PipelineDependencyError ---
python3 << 'PYEOF'
with open("cli.py", "r") as f:
    content = f.read()

if "PipelineDependencyError" not in content:
    # Add import after other imports
    old = "from pipeline_exceptions import PipelineError, PipelineDependencyError, PipelineValidationError"
    new = """from pipeline_exceptions import PipelineError, PipelineDependencyError, PipelineValidationError"""
    
    if old in content:
        # already has PipelineError, just add PipelineDependencyError
        pass
    else:
        # Find the import section
        import_line = "from pipeline_exceptions import PipelineError, PipelineValidationError"
        new_import = "from pipeline_exceptions import PipelineError, PipelineDependencyError, PipelineValidationError"
        content = content.replace(import_line, new_import)

with open("cli.py", "r") as f:
    current = f.read()

if "PipelineDependencyError" not in current:
    import_section = "from bem_ifc4 import write_ifc4"
    add_import = "from pipeline_exceptions import PipelineDependencyError, PipelineError, PipelineValidationError"
    if "from pipeline_exceptions import" not in current:
        # Find a good place to add the import
        lines = current.split("\n")
        new_lines = []
        for i, line in enumerate(lines):
            new_lines.append(line)
            if line.startswith("from bem_") or "from bem_" in line:
                if i+1 < len(lines) and not lines[i+1].startswith("from "):
                    new_lines.append(add_import)
        current = "\n".join(new_lines)
    with open("cli.py", "w") as f:
        f.write(current)
print("Checked cli.py")
PYEOF

git add -A && git commit -m "fix: graceful error when ifcopenshell import fails

Wrap ifcopenshell imports in try/except and raise PipelineDependencyError
with actionable reinstall hint instead of crashing with OSError.

Closes #407" 2>&1
git push -u origin $BRANCH 2>&1
echo "AGENT_407_DONE"
