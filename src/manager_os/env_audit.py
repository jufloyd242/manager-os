"""Environment variable audit tool for Manager OS.

Compares env vars referenced in code/settings to vars present in .env.example
and local .env.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from manager_os.config import Settings


def _get_code_env_vars() -> set[str]:
    """Extract all MANAGER_OS_ and GOOGLE_ env vars from source code."""
    repo_root = Path(__file__).parent.parent.parent
    vars_found: set[str] = set()
    
    # Patterns to match
    patterns = [
        r'MANAGER_OS_[A-Z0-9_]+',
        r'GOOGLE_[A-Z0-9_]+',
    ]
    
    # Directories to scan
    scan_dirs = [
        repo_root / "src" / "manager_os",
        repo_root / "tests",
    ]
    
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for py_file in scan_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                for pattern in patterns:
                    for match in re.finditer(pattern, content):
                        var_name = match.group(0)
                        # Filter out obvious non-env vars if any, but these patterns are specific
                        vars_found.add(var_name)
            except Exception:
                continue
                
    return vars_found


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple .env file into a dict."""
    env_vars = {}
    if not path.exists():
        return env_vars
        
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip().strip('"\'')
                
    return env_vars


def _get_settings_fields() -> set[str]:
    """Get all fields from pydantic Settings that have MANAGER_OS_ prefix."""
    fields = set()
    for field_name, field_info in Settings.model_fields.items():
        # We know the prefix is MANAGER_OS_ from config.py
        env_name = f"MANAGER_OS_{field_name.upper()}"
        fields.add(env_name)
    return fields


def run_audit(
    fix_local: bool = False,
    example_only: bool = False,
    as_json: bool = False,
) -> dict[str, Any]:
    """Run the environment audit."""
    repo_root = Path(__file__).parent.parent.parent
    example_path = repo_root / ".env.example"
    local_path = repo_root / ".env"
    
    code_vars = _get_code_env_vars()
    settings_vars = _get_settings_fields()
    all_expected = code_vars.union(settings_vars)
    
    example_vars = _parse_env_file(example_path)
    local_vars = _parse_env_file(local_path)
    
    # Analysis
    missing_from_example = all_expected - set(example_vars.keys())
    missing_from_local = set(example_vars.keys()) - set(local_vars.keys())
    unrecognized_in_local = set(local_vars.keys()) - all_expected
    
    # Deprecated candidates: in example but not in code/settings
    deprecated_candidates = set(example_vars.keys()) - all_expected
    
    result = {
        "missing_from_example": sorted(list(missing_from_example)),
        "missing_from_local": sorted(list(missing_from_local)),
        "unrecognized_in_local": sorted(list(unrecognized_in_local)),
        "deprecated_candidates": sorted(list(deprecated_candidates)),
        "total_expected": len(all_expected),
    }
    
    if fix_local and not example_only:
        # Add missing vars to local .env without overwriting
        if missing_from_local:
            with open(local_path, "a", encoding="utf-8") as f:
                f.write("\n# Added by env-audit --fix-local\n")
                for var in sorted(list(missing_from_local)):
                    # Get default from example
                    default_val = example_vars.get(var, "")
                    f.write(f"{var}={default_val}\n")
                    
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print("=== Manager OS Environment Audit ===\n")
        
        if result["missing_from_example"]:
            print("❌ Missing from .env.example:")
            for v in result["missing_from_example"]:
                print(f"  - {v}")
            print()
            
        if result["missing_from_local"]:
            print("⚠️  Missing from local .env:")
            for v in result["missing_from_local"]:
                print(f"  - {v}")
            print()
            
        if result["unrecognized_in_local"]:
            print("❓ Unrecognized in local .env (not in code/settings):")
            for v in result["unrecognized_in_local"]:
                print(f"  - {v}")
            print()
            
        if result["deprecated_candidates"]:
            print("🗑️  Deprecated candidates (in .env.example but not in code/settings):")
            for v in result["deprecated_candidates"]:
                print(f"  - {v}")
            print()
            
        if not any([result["missing_from_example"], result["missing_from_local"], 
                    result["unrecognized_in_local"], result["deprecated_candidates"]]):
            print("✅ Environment configuration is fully synchronized!\n")
            
    # Exit non-zero only if missing from example (required for new setups)
    # or if missing from local and we are not fixing
    exit_code = 0
    if result["missing_from_example"]:
        exit_code = 1
    elif result["missing_from_local"] and not fix_local:
        exit_code = 1
        
    return result, exit_code
