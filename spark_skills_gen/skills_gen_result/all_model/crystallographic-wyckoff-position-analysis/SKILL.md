---
name: crystallographic-wyckoff-position-analysis
description: Parse CIF files generated from crystallographic workflows, extract Wyckoff letters and multiplicities from symmetry analysis, and compute simplified representative fractional coordinates for the first site of each Wyckoff position.
tools:
  - python
  - pymatgen
  - sympy
tags:
  - materials-science
  - crystallography
  - cif
  - wyckoff
  - symmetry
  - pymatgen
  - fractional-coordinates
---

# Crystallographic Wyckoff Position Analysis

Use this skill when you need to process one or more CIF files and return:

1. A mapping from Wyckoff letter to multiplicity
2. A representative coordinate for the **first encountered atom** in each Wyckoff position
3. Coordinates formatted as simplified rational strings, typically with denominator bounded (e.g. `<= 12`)

This pattern is common in materials-science tasks where the validator expects a compact dictionary representation rather than a full symmetry report.

---

## When to Use This Approach

Choose this workflow when:

- Input is a CIF file from SHELX, Materials Project, or similar crystallographic source.
- You need Wyckoff letters like `a`, `b`, `c`, etc.
- You need multiplicities **as occupied in the parsed structure**, not a manually looked-up table from International Tables.
- You need approximate fractional coordinates rendered as rational strings.
- The result should be robust to small floating-point noise in CIF coordinates.

Prefer this approach over manual symmetry coding because:

- `pymatgen` already wraps reliable symmetry analysis.
- CIF parsing and symmetry tolerance handling are tricky to implement by hand.
- The task usually wants the symmetry assignment from the actual parsed structure.

Environment assumptions validated by prior success:

- Python 3.12
- `pymatgen`
- `sympy`

---

## Expected Output Shape

A typical return value should be:

```python
{
    "wyckoff_multiplicity_dict": {
        "a": 4,
        "c": 8
    },
    "wyckoff_coordinates_dict": {
        "a": ["0", "1/2", "1/2"],
        "c": ["3/8", "1/9", "8/9"]
    }
}
```

### Important output conventions

- Keys are Wyckoff **letters only**, not multiplicity-letter pairs like `4a`.
- Multiplicity values are integers.
- Coordinates are a list of 3 strings.
- Fractions should be simplified strings such as `"0"`, `"1"`, `"1/2"`, `"3/8"`.
- Coordinate approximation should use bounded denominators to avoid ugly fractions from CIF floating noise.

---

# High-Level Workflow

## 1) Parse the CIF as a full structure, not a primitive-reduced one

Why:

- Wyckoff assignments must match the actual site list used by the symmetry analyzer.
- Primitive reduction can change site ordering and multiplicity interpretation.
- In successful runs, parsing with `primitive=False` preserved the expected conventional/full-site representation.

Decision rule:

- Use `CifParser(...).parse_structures(primitive=False)`.
- Fail early if parsing returns no structures.

```python
from pathlib import Path
from pymatgen.io.cif import CifParser

def load_structure_from_cif(filepath: str):
    cif_path = Path(filepath)
    if not cif_path.exists():
        raise FileNotFoundError(f"CIF file not found: {cif_path}")

    parser = CifParser(str(cif_path))
    structures = parser.parse_structures(primitive=False)
    if not structures:
        raise ValueError(f"No structures parsed from CIF: {cif_path}")

    return structures[0]
```

---

## 2) Run symmetry analysis with `SpacegroupAnalyzer`

Why:

- Wyckoff letters are not directly reliable from raw CIF text in all cases.
- `pymatgen.symmetry.analyzer.SpacegroupAnalyzer` exposes symmetry datasets derived from the structure.
- The dataset contains per-site Wyckoff letters aligned to the sites being analyzed.

Decision rule:

- Use the parsed full structure.
- Read the symmetry dataset and extract `wyckoffs`.
- Confirm the number of returned Wyckoff labels matches the number of sites.

```python
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

def get_wyckoff_letters(structure):
    sga = SpacegroupAnalyzer(structure)
    dataset = sga.get_symmetry_dataset()

    if "wyckoffs" not in dataset:
        raise KeyError("Symmetry dataset does not contain 'wyckoffs'")

    wyckoffs = list(dataset["wyckoffs"])
    if len(wyckoffs) != len(structure):
        raise ValueError(
            f"Mismatch between wyckoff labels ({len(wyckoffs)}) and sites ({len(structure)})"
        )

    return wyckoffs
```

---

## 3) Count multiplicities by Wyckoff letter from the actual site list

Why:

- Many validators want the multiplicity represented by how many sites in the structure are assigned a given Wyckoff letter.
- This is safer than deriving multiplicity from space-group tables because CIF settings, cell choices, and occupancy representations can vary.

Decision rule:

- Iterate through `zip(structure, wyckoffs)`.
- Increment a count per letter.
- Preserve first occurrence order unless the task explicitly requires sorted output.

```python
from collections import OrderedDict

def count_wyckoff_multiplicities(structure, wyckoffs):
    multiplicity_dict = OrderedDict()

    for site, letter in zip(structure, wyckoffs):
        if not isinstance(letter, str) or len(letter) == 0:
            raise ValueError(f"Invalid Wyckoff letter: {letter!r}")
        multiplicity_dict[letter] = multiplicity_dict.get(letter, 0) + 1

    return dict(multiplicity_dict)
```

---

## 4) Capture the first encountered coordinate for each Wyckoff letter

Why:

- The task pattern asks for an approximate coordinate for the **first atom of each Wyckoff position**.
- Do not average all sites in a Wyckoff class.
- Do not standardize or symmetrize the coordinates unless explicitly requested.
- Using first occurrence aligned with parsed site order matched successful validation.

Decision rule:

- For each letter, store the first site's fractional coordinates only once.
- Normalize coordinates into `[0, 1)` before rationalization.

```python
def normalize_fractional_triplet(frac_coords):
    normalized = []
    for value in frac_coords:
        x = float(value) % 1.0
        if abs(x - 1.0) < 1e-12:
            x = 0.0
        normalized.append(x)
    return normalized

def first_wyckoff_coordinates(structure, wyckoffs):
    coord_dict = {}

    for site, letter in zip(structure, wyckoffs):
        if letter not in coord_dict:
            coord_dict[letter] = normalize_fractional_triplet(site.frac_coords)

    return coord_dict
```

---

## 5) Convert floating fractional coordinates to simple rational strings

Why:

- CIF coordinates often contain values like `0.33333333` or `0.874999999`.
- Validators often expect human-readable fractions rather than raw floats.
- Constraining denominators avoids pathological rationalizations from noisy decimals.

Decision rule:

- Use `sympy.Rational(...).limit_denominator(max_denominator)`.
- Normalize values first into `[0, 1)`.
- Snap tiny values near `0` or `1` to exact integers.
- Default `max_denominator=12` if the task requests âsimple resultâ fractions.

```python
from sympy import Rational

def float_to_fraction_string(value: float, max_denominator: int = 12) -> str:
    if max_denominator < 1:
        raise ValueError("max_denominator must be >= 1")

    x = float(value) % 1.0

    if abs(x) < 1e-10:
        return "0"
    if abs(x - 1.0) < 1e-10:
        return "1"

    frac = Rational(x).limit_denominator(max_denominator)

    # Defensive normalization in case the rational rounded up to 1
    if frac == 1:
        return "1"
    if frac == 0:
        return "0"

    if frac.q == 1:
        return str(frac.p)
    return f"{frac.p}/{frac.q}"

def rationalize_coordinate_triplet(frac_coords, max_denominator: int = 12):
    if len(frac_coords) != 3:
        raise ValueError(f"Expected 3 fractional coordinates, got {len(frac_coords)}")

    return [float_to_fraction_string(v, max_denominator=max_denominator) for v in frac_coords]
```

---

## 6) Assemble the final result in the exact required schema

Why:

- Many task validators compare dictionaries directly.
- Slight schema deviations often fail even when the scientific logic is correct.

Decision rule:

- Return exactly two top-level keys:
  - `wyckoff_multiplicity_dict`
  - `wyckoff_coordinates_dict`

```python
def build_wyckoff_result(structure, wyckoffs, max_denominator: int = 12):
    multiplicities = {}
    representative_coords = {}

    for site, letter in zip(structure, wyckoffs):
        multiplicities[letter] = multiplicities.get(letter, 0) + 1
        if letter not in representative_coords:
            representative_coords[letter] = rationalize_coordinate_triplet(
                normalize_fractional_triplet(site.frac_coords),
                max_denominator=max_denominator,
            )

    return {
        "wyckoff_multiplicity_dict": multiplicities,
        "wyckoff_coordinates_dict": representative_coords,
    }
```

---

## 7) Add robust error handling for batch workflows

Why:

- CIF parsing can emit warnings or fail on malformed input.
- A batch processing function should return a machine-readable error payload if required by the task pattern.
- This is especially useful if the task type allows returning `dict[str, Any]` on failure.

Decision rule:

- Catch exceptions at the public entry point.
- Return a structured error dict instead of crashing when the caller expects graceful behavior.

```python
from typing import Any

def analyze_wyckoff_position_multiplicities_and_coordinates(filepath: str) -> dict[str, dict] | dict[str, Any]:
    try:
        structure = load_structure_from_cif(filepath)
        wyckoffs = get_wyckoff_letters(structure)
        return build_wyckoff_result(structure, wyckoffs, max_denominator=12)
    except Exception as exc:
        return {
            "error": type(exc).__name__,
            "message": str(exc),
            "filepath": filepath,
        }
```

---

# Practical Notes

## Why `primitive=False` matters

A common hidden failure mode is parsing the CIF into a primitive cell and then wondering why:

- multiplicities do not match expectations,
- site counts change,
- representative coordinates correspond to a different setting/order.

For tasks that compare against expected Wyckoff counts from the full CIF representation, use:

```python
parser.parse_structures(primitive=False)
```

not:

```python
parser.parse_structures(primitive=True)
```

or any primitive conversion before extracting site-wise Wyckoff labels.

---

## Why first occurrence order is usually safest

If the evaluator compares dictionaries, insertion order can matter in some test harnesses even if Python dict equality ignores order. Also, the âfirst atom of each Wyckoff positionâ depends on site traversal order.

Safer pattern:

- iterate through the structure in parsed order,
- count letters in that order,
- store the first coordinate for each letter when first seen.

If the problem statement explicitly requires alphabetical sorting, sort before returning. Otherwise, preserve encounter order.

Example sorting variant:

```python
def sort_result_by_letter(result: dict) -> dict:
    mult = result["wyckoff_multiplicity_dict"]
    coords = result["wyckoff_coordinates_dict"]

    ordered_letters = sorted(mult.keys())
    return {
        "wyckoff_multiplicity_dict": {k: mult[k] for k in ordered_letters},
        "wyckoff_coordinates_dict": {k: coords[k] for k in ordered_letters},
    }
```

---

## How to inspect available CIF files in batch mode

```python
from pathlib import Path

def list_cif_files(directory: str):
    path = Path(directory)
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")
    return sorted(str(p) for p in path.glob("*.cif"))
```

---

## Batch processing example

```python
def analyze_cif_directory(directory: str):
    results = {}
    for filepath in list_cif_files(directory):
        results[Path(filepath).name] = analyze_wyckoff_position_multiplicities_and_coordinates(filepath)
    return results
```

---

# Complete Reference Implementation

## Reference Implementation

```python
from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from pymatgen.io.cif import CifParser
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from sympy import Rational


def load_structure_from_cif(filepath: str):
    """
    Load the first structure from a CIF file using the full, non-primitive representation.
    This is important for preserving the site list used for Wyckoff counting.
    """
    cif_path = Path(filepath)

    if not cif_path.exists():
        raise FileNotFoundError(f"CIF file not found: {cif_path}")
    if not cif_path.is_file():
        raise ValueError(f"Path is not a file: {cif_path}")

    parser = CifParser(str(cif_path))
    structures = parser.parse_structures(primitive=False)

    if not structures:
        raise ValueError(f"No structures were parsed from CIF: {cif_path}")

    return structures[0]


def get_site_wyckoff_letters(structure):
    """
    Return the per-site Wyckoff letters aligned with the structure's site order.
    """
    sga = SpacegroupAnalyzer(structure)
    dataset = sga.get_symmetry_dataset()

    if "wyckoffs" not in dataset:
        raise KeyError("Symmetry dataset missing 'wyckoffs'")

    wyckoffs = list(dataset["wyckoffs"])

    if len(wyckoffs) != len(structure):
        raise ValueError(
            f"Mismatch between number of wyckoff labels ({len(wyckoffs)}) "
            f"and number of sites ({len(structure)})"
        )

    for letter in wyckoffs:
        if not isinstance(letter, str) or not letter.strip():
            raise ValueError(f"Invalid Wyckoff letter encountered: {letter!r}")

    return wyckoffs


def normalize_fractional_value(value: float) -> float:
    """
    Normalize a fractional coordinate into [0, 1), snapping near-0/near-1 artifacts.
    """
    x = float(value) % 1.0

    if abs(x) < 1e-10:
        return 0.0
    if abs(x - 1.0) < 1e-10:
        return 0.0

    return x


def normalize_fractional_triplet(frac_coords) -> list[float]:
    """
    Normalize a length-3 fractional coordinate vector into [0, 1).
    """
    if len(frac_coords) != 3:
        raise ValueError(f"Expected 3 fractional coordinates, got {len(frac_coords)}")

    return [normalize_fractional_value(v) for v in frac_coords]


def float_to_fraction_string(value: float, max_denominator: int = 12) -> str:
    """
    Convert a float in fractional coordinates to a simple rational string.
    """
    if max_denominator < 1:
        raise ValueError("max_denominator must be >= 1")

    x = normalize_fractional_value(value)

    if abs(x) < 1e-10:
        return "0"

    frac = Rational(x).limit_denominator(max_denominator)

    # Guard against rationalization artifacts
    if frac == 0:
        return "0"
    if frac == 1:
        return "1"

    if frac.q == 1:
        return str(frac.p)
    return f"{frac.p}/{frac.q}"


def triplet_to_fraction_strings(frac_coords, max_denominator: int = 12) -> list[str]:
    """
    Convert a 3-vector fractional coordinate to a list of rational strings.
    """
    normalized = normalize_fractional_triplet(frac_coords)
    return [float_to_fraction_string(v, max_denominator=max_denominator) for v in normalized]


def analyze_wyckoff_position_multiplicities_and_coordinates(filepath: str) -> dict[str, dict] | dict[str, Any]:
    """
    Analyze a CIF file and return:
      - wyckoff_multiplicity_dict: counts of sites by Wyckoff letter
      - wyckoff_coordinates_dict: first encountered site coordinates by Wyckoff letter,
        converted to rational strings with bounded denominator

    On failure, returns a structured error dictionary.
    """
    try:
        structure = load_structure_from_cif(filepath)
        wyckoffs = get_site_wyckoff_letters(structure)

        multiplicity_dict: dict[str, int] = {}
        coordinates_dict: dict[str, list[str]] = {}

        for site, letter in zip(structure, wyckoffs):
            multiplicity_dict[letter] = multiplicity_dict.get(letter, 0) + 1

            if letter not in coordinates_dict:
                coordinates_dict[letter] = triplet_to_fraction_strings(
                    site.frac_coords,
                    max_denominator=12,
                )

        return {
            "wyckoff_multiplicity_dict": multiplicity_dict,
            "wyckoff_coordinates_dict": coordinates_dict,
        }

    except Exception as exc:
        return {
            "error": type(exc).__name__,
            "message": str(exc),
            "filepath": filepath,
        }


def analyze_cif_directory(directory: str) -> dict[str, dict[str, dict] | dict[str, Any]]:
    """
    Batch-process all .cif files in a directory.
    """
    dir_path = Path(directory)

    if not dir_path.exists():
        raise FileNotFoundError(f"Directory not found: {dir_path}")
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    results: dict[str, dict[str, dict] | dict[str, Any]] = {}

    for cif_file in sorted(dir_path.glob("*.cif")):
        results[cif_file.name] = analyze_wyckoff_position_multiplicities_and_coordinates(str(cif_file))

    return results


if __name__ == "__main__":
    import sys

    # Usage:
    #   python solution.py /path/to/file.cif
    #   python solution.py /path/to/cif_directory
    #
    # Prints JSON for easy inspection and validator integration.

    if len(sys.argv) != 2:
        print("Usage: python solution.py <cif-file-or-directory>")
        raise SystemExit(2)

    target = Path(sys.argv[1])

    if target.is_file():
        result = analyze_wyckoff_position_multiplicities_and_coordinates(str(target))
    elif target.is_dir():
        result = analyze_cif_directory(str(target))
    else:
        result = {
            "error": "FileNotFoundError",
            "message": f"Path does not exist: {target}",
            "filepath": str(target),
        }

    print(json.dumps(result, indent=2, sort_keys=False))
```

---

# Validation Workflow

Before finalizing, run these checks.

## 1) Smoke test a single CIF

```python
from pathlib import Path

sample = Path("/path/to/sample.cif")
result = analyze_wyckoff_position_multiplicities_and_coordinates(str(sample))
print(result)
```

What to verify:

- top-level keys are correct,
- Wyckoff letters look plausible,
- each coordinate is a 3-string list,
- no raw floats remain in output.

---

## 2) Verify all multiplicities sum to the number of parsed sites

```python
def validate_site_count_consistency(filepath: str):
    structure = load_structure_from_cif(filepath)
    wyckoffs = get_site_wyckoff_letters(structure)
    result = analyze_wyckoff_position_multiplicities_and_coordinates(filepath)

    if "error" in result:
        raise RuntimeError(result)

    counted = sum(result["wyckoff_multiplicity_dict"].values())
    actual = len(structure)

    return {
        "filepath": filepath,
        "site_count": actual,
        "counted_multiplicity_sum": counted,
        "matches": counted == actual,
        "wyckoff_count": len(wyckoffs),
    }
```

---

## 3) Check that representative coordinates correspond to the first encountered site

```python
def inspect_first_site_per_letter(filepath: str):
    structure = load_structure_from_cif(filepath)
    wyckoffs = get_site_wyckoff_letters(structure)

    seen = {}
    for i, (site, letter) in enumerate(zip(structure, wyckoffs)):
        if letter not in seen:
            seen[letter] = {
                "site_index": i,
                "raw_frac_coords": [float(x) for x in site.frac_coords],
                "normalized_frac_coords": normalize_fractional_triplet(site.frac_coords),
                "fraction_strings": triplet_to_fraction_strings(site.frac_coords, max_denominator=12),
            }
    return seen
```

---

## 4) Batch-verify a directory

```python
from pprint import pprint

directory_results = analyze_cif_directory("/path/to/cif_files")
pprint(directory_results)
```

Look for:

- unexpected error dicts,
- letters missing where sites exist,
- coordinates with strange large denominators,
- inconsistent site counts.

---

# Common Pitfalls

## 1) Parsing as primitive structure
**Problem:** Multiplicities and Wyckoff counts no longer match expected results.

**Avoid it:** Always start with:

```python
CifParser(path).parse_structures(primitive=False)
```

unless the task explicitly asks for primitive-cell analysis.

---

## 2) Using standardized/symmetrized coordinates instead of the original first site
**Problem:** Representative coordinates differ from expected values.

**Avoid it:** Store the first encountered site for each Wyckoff letter directly from the parsed structure order. Do not replace it with a symmetrized coordinate unless requested.

---

## 3) Deriving multiplicity from space-group tables instead of counting sites
**Problem:** Returned multiplicities disagree with actual parsed CIF content.

**Avoid it:** Count how many parsed sites carry each Wyckoff letter in the symmetry dataset.

---

## 4) Returning floats instead of rational strings
**Problem:** Output fails exact-match validators.

**Avoid it:** Convert each fractional coordinate with bounded rationalization, e.g. denominator `<= 12`.

---

## 5) Forgetting to normalize coordinates into `[0, 1)`
**Problem:** Coordinates like `-0.125` or `1.0` appear, causing formatting or comparison failures.

**Avoid it:** Normalize with modulo before rationalization.

---

## 6) Sorting letters when the task wants first occurrence semantics
**Problem:** âFirst atom of each Wyckoff positionâ becomes ambiguous or mismatched.

**Avoid it:** Preserve insertion order by default. Only sort if the task explicitly says so.

---

## 7) Ignoring CIF parse edge cases
**Problem:** Batch scripts crash on malformed or noisy CIF files.

**Avoid it:** Catch exceptions in the public entry point and return a structured error dict when appropriate.

---

# Heuristics for Choosing the Right Execution Path Early

If you see a task asking for:

- CIF input
- Wyckoff letters
- multiplicities
- approximate coordinates
- simplified fractions

then the fastest reliable path is:

1. Parse with `pymatgen.io.cif.CifParser`
2. Analyze with `pymatgen.symmetry.analyzer.SpacegroupAnalyzer`
3. Read `dataset["wyckoffs"]`
4. Count per letter from the site list
5. Store the first encountered coordinate per letter
6. Rationalize with `sympy.Rational(...).limit_denominator(12)`

This path has high transfer value and avoids overengineering.

---

# Minimal Public Function Template

If you only need the entry function body, this is the shortest safe pattern:

```python
def analyze_wyckoff_position_multiplicities_and_coordinates(filepath: str):
    try:
        structure = load_structure_from_cif(filepath)
        wyckoffs = get_site_wyckoff_letters(structure)

        multiplicity_dict = {}
        coordinates_dict = {}

        for site, letter in zip(structure, wyckoffs):
            multiplicity_dict[letter] = multiplicity_dict.get(letter, 0) + 1
            if letter not in coordinates_dict:
                coordinates_dict[letter] = triplet_to_fraction_strings(site.frac_coords, max_denominator=12)

        return {
            "wyckoff_multiplicity_dict": multiplicity_dict,
            "wyckoff_coordinates_dict": coordinates_dict,
        }
    except Exception as exc:
        return {
            "error": type(exc).__name__,
            "message": str(exc),
            "filepath": filepath,
        }
```

---

# Final Checklist

Before submitting:

- [ ] Uses `pymatgen` rather than manual CIF parsing
- [ ] Parses with `primitive=False`
- [ ] Extracts `dataset["wyckoffs"]`
- [ ] Counts multiplicities from actual site assignments
- [ ] Stores first encountered site per Wyckoff letter
- [ ] Rationalizes coordinates with bounded denominator
- [ ] Returns exact schema required by the task
- [ ] Handles file/path/parser errors gracefully
- [ ] Optionally tested on all CIFs in the input directory

This workflow is the default strong solution for CIF-based Wyckoff multiplicity and representative-coordinate extraction tasks.