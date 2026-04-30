---
name: npm-package-lock-high-critical-vulnerability-audit
description: Audit a Node.js package-lock.json for HIGH and CRITICAL third-party dependency vulnerabilities, then write a normalized CSV report with package, version, CVE, severity, CVSS, fixed version, title, and URL.
tags:
  - security
  - dependency-audit
  - npm
  - package-lock
  - vulnerability-management
  - csv
tools:
  - python
  - node
  - bash
  - trivy
  - npm
---

# npm `package-lock.json` HIGH/CRITICAL Vulnerability Audit

This skill teaches an agent how to audit a Node.js dependency lockfile, extract only **HIGH** and **CRITICAL** vulnerabilities, and write a validator-friendly CSV.

It is designed for tasks where:

- the input is a `package-lock.json`
- the output must be a CSV with exact columns
- only severe vulnerabilities matter
- the environment may be partially offline
- the agent must avoid over-reporting, under-reporting, or formatting mistakes

---

## When to Use This Skill

Use this skill when you are given:

- a Node.js lockfile such as `package-lock.json`
- instructions to identify vulnerable third-party packages
- a requirement to report:
  - package name
  - installed version
  - CVE
  - severity
  - CVSS
  - fixed version
  - title/description
  - reference URL
- a required CSV output

This is especially relevant when the task emphasizes:

- **offline tools or local databases**
- **only HIGH/CRITICAL findings**
- **exact output schema**
- **ground-truth-sensitive validation**

---

## Output Contract

Write a CSV with exactly these headers and order:

```csv
Package,Version,CVE_ID,Severity,CVSS_Score,Fixed_Version,Title,Url
```

Rules:

- `Severity` must be normalized to `HIGH` or `CRITICAL`
- `CVE_ID` should prefer actual CVE IDs when present
- if no fixed version is available, write `N/A`
- `CVSS_Score` should be a numeric-looking string when available
- each row should correspond to one concrete vulnerable installed package/version
- avoid blank required fields unless the task explicitly allows them

---

# High-Level Workflow

## 1) Inspect the lockfile structure before choosing a parsing strategy

Why:
`package-lock.json` differs across npm versions. Some files use a top-level `packages` map, while older ones use nested `dependencies`. You must know which structure you are dealing with before extracting package/version pairs.

Decision criteria:

- If `lockfileVersion >= 2` and `packages` exists, prefer `packages`
- Otherwise, recursively walk `dependencies`
- Ignore the root project package entry (`""` in `packages`)

### Example: inspect lockfile shape

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path("/root/package-lock.json")
if not path.exists():
    raise SystemExit(f"Missing lockfile: {path}")

with path.open("r", encoding="utf-8") as f:
    lock = json.load(f)

print("name:", lock.get("name"))
print("lockfileVersion:", lock.get("lockfileVersion"))
print("has_packages:", isinstance(lock.get("packages"), dict))
print("has_dependencies:", isinstance(lock.get("dependencies"), dict))

if isinstance(lock.get("packages"), dict):
    sample = list(lock["packages"].keys())[:10]
    print("sample package keys:", sample)
elif isinstance(lock.get("dependencies"), dict):
    sample = list(lock["dependencies"].keys())[:10]
    print("sample dependency keys:", sample)
else:
    print("No recognizable dependency structure found")
PY
```

---

## 2) Enumerate installed packages and versions from the lockfile

Why:
Before validating findings, you need the exact installed package versions. Vulnerability tools may report package names, but the task usually expects the installed version from the lockfile.

Decision criteria:

- Use `packages["node_modules/<name>"]` entries when available
- Fall back to recursively traversing nested `dependencies`
- Deduplicate `(package, version)` pairs
- Preserve package scopes like `@scope/name`

### Example: robust lockfile package extraction

```python
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

def extract_installed_packages(lockfile_path: str) -> List[Dict[str, str]]:
    path = Path(lockfile_path)
    if not path.exists():
        raise FileNotFoundError(f"Lockfile not found: {lockfile_path}")

    with path.open("r", encoding="utf-8") as f:
        lock = json.load(f)

    found: Set[Tuple[str, str]] = set()

    packages = lock.get("packages")
    if isinstance(packages, dict):
        for pkg_path, meta in packages.items():
            if not pkg_path or pkg_path == "":
                continue
            if not pkg_path.startswith("node_modules/"):
                continue

            name = pkg_path.split("node_modules/", 1)[1]
            version = (meta or {}).get("version")
            if name and version:
                found.add((name, str(version)))
    else:
        def walk(dep_map: Dict[str, dict]) -> None:
            for name, meta in dep_map.items():
                if not isinstance(meta, dict):
                    continue
                version = meta.get("version")
                if name and version:
                    found.add((name, str(version)))
                nested = meta.get("dependencies")
                if isinstance(nested, dict):
                    walk(nested)

        deps = lock.get("dependencies", {})
        if isinstance(deps, dict):
            walk(deps)

    return [
        {"Package": name, "Version": version}
        for name, version in sorted(found)
    ]


if __name__ == "__main__":
    pkgs = extract_installed_packages("/root/package-lock.json")
    print(f"package_count={len(pkgs)}")
    for row in pkgs[:20]:
        print(row)
```

---

## 3) Prefer an offline-capable vulnerability source and normalize the result

Why:
The task may explicitly say âuse offline tools or database.â Tool output formats vary, so you need a normalization layer.

Preferred strategy:

1. **Use Trivy** if installed and a vulnerability DB is already available locally
2. If Trivy is unavailable or unsuitable, use another local advisory source already present in the environment
3. Only use network-backed tooling if the task allows it and local/offline methods are insufficient

Decision criteria:

- If the task says offline, use `trivy` with `--skip-db-update`
- If Trivy errors because there is no local DB cache, do not blindly hang or loop forever
- If you must inspect candidates manually, cross-check exact installed versions and only report verifiable HIGH/CRITICAL issues

### Example: run Trivy against the project directory using local DB only

```bash
set -euo pipefail

WORKDIR="/root"
OUT_JSON="/tmp/trivy_npm_audit.json"

if ! command -v trivy >/dev/null 2>&1; then
  echo "trivy not installed" >&2
  exit 1
fi

# --skip-db-update avoids network refresh attempts in offline tasks.
# --pkg-types library focuses on third-party packages.
# --format json makes downstream parsing deterministic.
trivy fs \
  --quiet \
  --skip-db-update \
  --scanners vuln \
  --pkg-types library \
  --format json \
  --output "$OUT_JSON" \
  "$WORKDIR" || {
    echo "Trivy scan failed; check whether a local DB cache exists" >&2
    exit 2
  }

python - <<'PY'
import json
from pathlib import Path

p = Path("/tmp/trivy_npm_audit.json")
if not p.exists():
    raise SystemExit("Missing Trivy output JSON")

data = json.loads(p.read_text(encoding="utf-8"))
print("top-level type:", type(data).__name__)
if isinstance(data, list):
    print("result_count:", len(data))
    for result in data[:3]:
        print("target:", result.get("Target"))
        vulns = result.get("Vulnerabilities") or []
        print("vulnerability_count:", len(vulns))
PY
```

---

## 4) Filter to HIGH/CRITICAL and map fields to the required schema

Why:
Scanner output rarely matches the target CSV schema exactly. You must transform fields carefully.

Mapping guidance:

- `Package` â package/library name
- `Version` â installed version
- `CVE_ID` â prefer a CVE from aliases or vulnerability ID; if none, fall back only if task allows
- `Severity` â uppercase
- `CVSS_Score` â choose the best available score from vendor/NVD/source data
- `Fixed_Version` â scanner fixed version if present, else `N/A`
- `Title` â vulnerability title, else description summary
- `Url` â primary reference URL

### Example: normalize Trivy JSON findings

```python
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

ALLOWED = {"HIGH", "CRITICAL"}

def pick_cve(vuln: Dict[str, Any]) -> str:
    vid = str(vuln.get("VulnerabilityID") or "").strip()
    if vid.startswith("CVE-"):
        return vid

    for alias in vuln.get("PrimaryURL", ""), *vuln.get("References", []):
        text = str(alias)
        if "CVE-" in text:
            # Lightweight extraction
            parts = text.replace("/", " ").replace("?", " ").replace("&", " ").split()
            for part in parts:
                if part.startswith("CVE-"):
                    return part.strip(" ,;.)]}>\"'")
    aliases = vuln.get("Aliases") or []
    for alias in aliases:
        alias = str(alias).strip()
        if alias.startswith("CVE-"):
            return alias

    return vid or "N/A"

def pick_cvss(vuln: Dict[str, Any]) -> str:
    cvss = vuln.get("CVSS") or {}
    best: Optional[float] = None

    if isinstance(cvss, dict):
        for _, entry in cvss.items():
            if isinstance(entry, dict):
                score = entry.get("V3Score")
                if score is None:
                    score = entry.get("Score")
                try:
                    score = float(score)
                    if best is None or score > best:
                        best = score
                except (TypeError, ValueError):
                    pass

    if best is None:
        sev_score = vuln.get("CVSSScore")
        try:
            best = float(sev_score)
        except (TypeError, ValueError):
            pass

    return f"{best:.1f}" if best is not None else "N/A"

def normalize_trivy_results(path: str) -> List[Dict[str, str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows: List[Dict[str, str]] = []

    for result in data if isinstance(data, list) else []:
        for vuln in result.get("Vulnerabilities") or []:
            severity = str(vuln.get("Severity") or "").upper().strip()
            if severity not in ALLOWED:
                continue

            pkg = str(vuln.get("PkgName") or "").strip()
            version = str(vuln.get("InstalledVersion") or "").strip()
            if not pkg or not version:
                continue

            title = str(vuln.get("Title") or vuln.get("Description") or "").strip()
            url = str(vuln.get("PrimaryURL") or "").strip()
            fixed = str(vuln.get("FixedVersion") or "").strip() or "N/A"

            rows.append({
                "Package": pkg,
                "Version": version,
                "CVE_ID": pick_cve(vuln),
                "Severity": severity,
                "CVSS_Score": pick_cvss(vuln),
                "Fixed_Version": fixed,
                "Title": title or "N/A",
                "Url": url or "N/A",
            })

    return rows


if __name__ == "__main__":
    for row in normalize_trivy_results("/tmp/trivy_npm_audit.json")[:10]:
        print(row)
```

---

## 5) Cross-check findings against the lockfile to avoid phantom or mismatched entries

Why:
Some tools can report transitive findings in ways that need validation. Your final CSV must reflect actual installed packages and versions from the lockfile.

Decision criteria:

- Keep only findings where `(Package, Version)` exists in the lockfile inventory
- Deduplicate rows on `(Package, Version, CVE_ID)`
- Do not invent fixed versions or CVSS scores

### Example: validate findings against extracted package inventory

```python
from typing import Dict, List, Set, Tuple

def filter_against_inventory(
    findings: List[Dict[str, str]],
    inventory: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    allowed: Set[Tuple[str, str]] = {
        (row["Package"], row["Version"]) for row in inventory
    }
    seen: Set[Tuple[str, str, str]] = set()
    kept: List[Dict[str, str]] = []

    for row in findings:
        key_pkg = (row["Package"], row["Version"])
        key_full = (row["Package"], row["Version"], row["CVE_ID"])

        if key_pkg not in allowed:
            continue
        if key_full in seen:
            continue

        seen.add(key_full)
        kept.append(row)

    kept.sort(key=lambda r: (
        r["Severity"] != "CRITICAL",
        r["Package"].lower(),
        r["Version"],
        r["CVE_ID"]
    ))
    return kept
```

---

## 6) Write the CSV with exact headers and verify it parses cleanly

Why:
Many tasks fail due to formatting, not detection. CSV must have exact headers, correct quoting, and no malformed rows.

Decision criteria:

- Always use Python's `csv.DictWriter`
- Always verify by re-reading the CSV
- Ensure required fields are non-empty where expected

### Example: write and verify the final CSV

```python
import csv
from pathlib import Path
from typing import Dict, List

HEADERS = [
    "Package",
    "Version",
    "CVE_ID",
    "Severity",
    "CVSS_Score",
    "Fixed_Version",
    "Title",
    "Url",
]

def write_csv(rows: List[Dict[str, str]], out_path: str) -> None:
    path = Path(out_path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            normalized = {k: str(row.get(k, "") or "") for k in HEADERS}
            writer.writerow(normalized)

def verify_csv(out_path: str) -> None:
    path = Path(out_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not created: {out_path}")

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != HEADERS:
            raise ValueError(f"Unexpected headers: {reader.fieldnames}")
        rows = list(reader)

    for i, row in enumerate(rows, start=2):
        for required in ("Package", "Version", "CVE_ID", "Severity"):
            if not str(row.get(required, "")).strip():
                raise ValueError(f"Missing {required} in CSV row {i}")

        severity = str(row["Severity"]).upper().strip()
        if severity not in {"HIGH", "CRITICAL"}:
            raise ValueError(f"Invalid severity in row {i}: {row['Severity']}")

    print(f"verified_rows={len(rows)}")


if __name__ == "__main__":
    sample = [{
        "Package": "example",
        "Version": "1.0.0",
        "CVE_ID": "CVE-2099-0001",
        "Severity": "HIGH",
        "CVSS_Score": "7.5",
        "Fixed_Version": "1.0.1",
        "Title": "Example issue",
        "Url": "https://example.invalid/advisory"
    }]
    write_csv(sample, "/tmp/security_audit.csv")
    verify_csv("/tmp/security_audit.csv")
```

---

## 7) Final sanity checks before submission

Why:
Even a technically correct scan can fail a grader if the output includes wrong severities, missing CVEs, or mismatched columns.

Check these before finalizing:

- only `HIGH` and `CRITICAL`
- exact header order
- no duplicate rows
- package/version pairs exist in the lockfile
- no extra commentary in the CSV
- fields with commas are properly quoted by the CSV writer
- fixed version is `N/A` only when genuinely unavailable

### Example: quick end-state validator

```bash
python - <<'PY'
import csv
from pathlib import Path

path = Path("/root/security_audit.csv")
if not path.exists():
    raise SystemExit("security_audit.csv is missing")

with path.open("r", newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

expected = [
    "Package","Version","CVE_ID","Severity",
    "CVSS_Score","Fixed_Version","Title","Url"
]

if rows:
    with path.open("r", newline="", encoding="utf-8") as f:
        headers = csv.DictReader(f).fieldnames
    if headers != expected:
        raise SystemExit(f"Header mismatch: {headers}")

for idx, row in enumerate(rows, start=2):
    sev = (row.get("Severity") or "").upper().strip()
    if sev not in {"HIGH", "CRITICAL"}:
        raise SystemExit(f"Unexpected severity at row {idx}: {sev}")
    for col in ("Package", "Version", "CVE_ID", "Severity"):
        if not (row.get(col) or "").strip():
            raise SystemExit(f"Missing {col} at row {idx}")

print(f"CSV looks valid; row_count={len(rows)}")
PY
```

---

# Reference Implementation

The following script is the recommended end-to-end implementation. It:

1. reads `package-lock.json`
2. extracts installed packages
3. runs Trivy in offline mode if possible
4. normalizes HIGH/CRITICAL findings
5. validates findings against the lockfile inventory
6. writes `/root/security_audit.csv`
7. verifies the resulting CSV

Copy, run, and adapt as needed.

```python
#!/usr/bin/env python3
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

LOCKFILE = "/root/package-lock.json"
OUTPUT_CSV = "/root/security_audit.csv"
TRIVY_JSON = "/tmp/trivy_npm_audit.json"

HEADERS = [
    "Package",
    "Version",
    "CVE_ID",
    "Severity",
    "CVSS_Score",
    "Fixed_Version",
    "Title",
    "Url",
]

ALLOWED_SEVERITIES = {"HIGH", "CRITICAL"}


def load_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def extract_installed_packages(lockfile_path: str) -> List[Dict[str, str]]:
    lock = load_json(lockfile_path)
    found: Set[Tuple[str, str]] = set()

    packages = lock.get("packages")
    if isinstance(packages, dict):
        for pkg_path, meta in packages.items():
            if not pkg_path or pkg_path == "":
                continue
            if not pkg_path.startswith("node_modules/"):
                continue
            if not isinstance(meta, dict):
                continue

            name = pkg_path.split("node_modules/", 1)[1]
            version = meta.get("version")
            if name and version:
                found.add((name, str(version)))
    else:
        def walk(dep_map: Dict[str, Any]) -> None:
            for name, meta in dep_map.items():
                if not isinstance(meta, dict):
                    continue
                version = meta.get("version")
                if name and version:
                    found.add((name, str(version)))
                nested = meta.get("dependencies")
                if isinstance(nested, dict):
                    walk(nested)

        deps = lock.get("dependencies", {})
        if isinstance(deps, dict):
            walk(deps)

    rows = [{"Package": name, "Version": version} for name, version in sorted(found)]
    if not rows:
        raise ValueError("No installed packages extracted from lockfile")
    return rows


def run_trivy_scan(workdir: str, output_json: str) -> None:
    cmd = [
        "trivy", "fs",
        "--quiet",
        "--skip-db-update",
        "--scanners", "vuln",
        "--pkg-types", "library",
        "--format", "json",
        "--output", output_json,
        workdir,
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("trivy is not installed in the environment") from e

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        msg = "\n".join(part for part in [stdout, stderr] if part)
        raise RuntimeError(
            "Trivy scan failed. This may mean no offline DB is available.\n" + msg
        )

    if not Path(output_json).exists():
        raise RuntimeError("Trivy completed without producing JSON output")


def pick_cve(vuln: Dict[str, Any]) -> str:
    vid = str(vuln.get("VulnerabilityID") or "").strip()
    if vid.startswith("CVE-"):
        return vid

    aliases = vuln.get("Aliases") or []
    for alias in aliases:
        alias = str(alias).strip()
        if alias.startswith("CVE-"):
            return alias

    refs = []
    primary = vuln.get("PrimaryURL")
    if primary:
        refs.append(str(primary))
    refs.extend(str(x) for x in (vuln.get("References") or []))

    for text in refs:
        tokens = (
            text.replace("/", " ")
            .replace("?", " ")
            .replace("&", " ")
            .replace("=", " ")
            .replace(",", " ")
            .split()
        )
        for token in tokens:
            token = token.strip("()[]{}<>\"' ")
            if token.startswith("CVE-"):
                return token

    return vid or "N/A"


def pick_cvss(vuln: Dict[str, Any]) -> str:
    best: Optional[float] = None

    cvss = vuln.get("CVSS")
    if isinstance(cvss, dict):
        for _, entry in cvss.items():
            if not isinstance(entry, dict):
                continue
            score = entry.get("V3Score")
            if score is None:
                score = entry.get("Score")
            try:
                score_f = float(score)
                if best is None or score_f > best:
                    best = score_f
            except (TypeError, ValueError):
                pass

    if best is None:
        for key in ("CVSSScore", "SeverityScore"):
            score = vuln.get(key)
            try:
                score_f = float(score)
                if best is None or score_f > best:
                    best = score_f
            except (TypeError, ValueError):
                pass

    return f"{best:.1f}" if best is not None else "N/A"


def normalize_title(vuln: Dict[str, Any]) -> str:
    title = str(vuln.get("Title") or "").strip()
    if title:
        return title

    desc = str(vuln.get("Description") or "").strip()
    if not desc:
        return "N/A"

    desc = " ".join(desc.split())
    if len(desc) <= 240:
        return desc
    return desc[:237] + "..."


def normalize_trivy_results(path: str) -> List[Dict[str, str]]:
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError("Unexpected Trivy JSON structure; expected a list")

    rows: List[Dict[str, str]] = []

    for result in data:
        vulns = result.get("Vulnerabilities") or []
        if not isinstance(vulns, list):
            continue

        for vuln in vulns:
            if not isinstance(vuln, dict):
                continue

            severity = str(vuln.get("Severity") or "").upper().strip()
            if severity not in ALLOWED_SEVERITIES:
                continue

            pkg = str(vuln.get("PkgName") or "").strip()
            version = str(vuln.get("InstalledVersion") or "").strip()

            if not pkg or not version:
                continue

            row = {
                "Package": pkg,
                "Version": version,
                "CVE_ID": pick_cve(vuln),
                "Severity": severity,
                "CVSS_Score": pick_cvss(vuln),
                "Fixed_Version": str(vuln.get("FixedVersion") or "").strip() or "N/A",
                "Title": normalize_title(vuln),
                "Url": str(vuln.get("PrimaryURL") or "").strip() or "N/A",
            }
            rows.append(row)

    return rows


def filter_against_inventory(
    findings: List[Dict[str, str]],
    inventory: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    allowed_pairs: Set[Tuple[str, str]] = {
        (row["Package"], row["Version"]) for row in inventory
    }

    seen: Set[Tuple[str, str, str]] = set()
    kept: List[Dict[str, str]] = []

    for row in findings:
        pair = (row["Package"], row["Version"])
        key = (row["Package"], row["Version"], row["CVE_ID"])

        if pair not in allowed_pairs:
            continue
        if key in seen:
            continue

        seen.add(key)
        kept.append(row)

    kept.sort(key=lambda r: (
        0 if r["Severity"] == "CRITICAL" else 1,
        r["Package"].lower(),
        r["Version"],
        r["CVE_ID"],
    ))
    return kept


def write_csv(rows: List[Dict[str, str]], out_path: str) -> None:
    p = Path(out_path)
    with p.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: str(row.get(h, "") or "") for h in HEADERS})


def verify_csv(out_path: str) -> None:
    p = Path(out_path)
    if not p.exists():
        raise FileNotFoundError(f"Output CSV missing: {out_path}")

    with p.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != HEADERS:
            raise ValueError(f"Unexpected headers: {reader.fieldnames}")
        rows = list(reader)

    for i, row in enumerate(rows, start=2):
        for required in ("Package", "Version", "CVE_ID", "Severity"):
            if not str(row.get(required, "")).strip():
                raise ValueError(f"Missing {required} in row {i}")

        severity = str(row["Severity"]).upper().strip()
        if severity not in ALLOWED_SEVERITIES:
            raise ValueError(f"Invalid severity {severity} in row {i}")

    print(f"Verified CSV with {len(rows)} rows: {out_path}")


def main() -> int:
    try:
        inventory = extract_installed_packages(LOCKFILE)
        print(f"Extracted {len(inventory)} installed packages from lockfile")

        run_trivy_scan("/root", TRIVY_JSON)
        raw_findings = normalize_trivy_results(TRIVY_JSON)
        print(f"Normalized {len(raw_findings)} HIGH/CRITICAL findings from Trivy")

        final_rows = filter_against_inventory(raw_findings, inventory)
        print(f"Kept {len(final_rows)} findings after inventory validation")

        write_csv(final_rows, OUTPUT_CSV)
        verify_csv(OUTPUT_CSV)

        return 0

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Run it:

```bash
python /tmp/audit_package_lock.py
```

If you want to create and run it in one shot:

```bash
cat > /tmp/audit_package_lock.py <<'PY'
# paste the full script here
PY
python /tmp/audit_package_lock.py
```

---

# Practical Notes

## Lockfile structure conventions

### `lockfileVersion` 2 or 3
Typically includes:

- top-level `packages`
- keys like:
  - `""` for the root project
  - `node_modules/foo`
  - `node_modules/@scope/bar`

Use these for exact installed versions.

### `lockfileVersion` 1
Usually relies on nested:

- `dependencies`
- each dependency may include nested `dependencies`

Use recursive traversal.

---

## Severity and scoring conventions

When multiple score sources exist:

- prefer a numeric CVSS score
- if several scores are present, choose the highest authoritative available score exposed by the scanner
- normalize to a simple decimal string like `7.5` or `9.8`

For severity:

- uppercase values
- include only `HIGH` and `CRITICAL`

---

## Reference URL conventions

Best sources include:

- NVD
- GHSA
- Red Hat
- vendor advisory pages
- scanner advisory pages such as Aqua/Trivy AVD when that is the available local source

Use a stable URL already present in the vulnerability result when possible.

---

## Fixed version conventions

Use:

- exact fixed version string if the scanner provides one
- version range/list only if that is how the advisory expresses fixes
- `N/A` if no fix is available or no fixed version is published

Do **not** invent a fix.

---

# Common Pitfalls

## 1) Using the wrong dependency source from `package-lock.json`
Mistake:
Only checking top-level dependencies or package manifest files.

Why it fails:
The task is about the installed dependency graph captured by the lockfile, not just declared dependencies.

Avoid it:
Parse `packages` or recursively traverse nested `dependencies`.

---

## 2) Reporting LOW/MEDIUM findings
Mistake:
Dumping all vulnerabilities from the scanner.

Why it fails:
The task explicitly requires **only HIGH and CRITICAL**.

Avoid it:
Filter strictly on normalized uppercase severity.

---

## 3) Trusting scanner output without validating package/version presence
Mistake:
Writing findings directly to CSV.

Why it fails:
You can end up with rows for packages or versions not actually present in the lockfile snapshot.

Avoid it:
Cross-check `(Package, Version)` against the extracted inventory.

---

## 4) Producing malformed CSV
Mistake:
Manually concatenating lines, especially when titles or fixed versions contain commas.

Why it fails:
CSV parsing breaks, headers mismatch, or rows shift columns.

Avoid it:
Always use `csv.DictWriter` and re-read the file to verify.

---

## 5) Leaving required fields blank
Mistake:
Writing empty `CVE_ID`, `Severity`, or `Version`.

Why it fails:
Many validators require non-empty fields for core columns.

Avoid it:
Prefer verifiable CVEs; if no fixed version exists use `N/A`, but do not blank required fields.

---

## 6) Depending on online updates in an offline task
Mistake:
Running tools that block on network or try to update advisory databases.

Why it fails:
Scans may hang, error, or violate task constraints.

Avoid it:
Use offline-capable tooling like `trivy --skip-db-update`, and prefer already-cached/local advisory data.

---

## 7) Confusing package name with path
Mistake:
Writing `node_modules/foo` instead of `foo`.

Why it fails:
The expected package field is the package name, not the lockfile path.

Avoid it:
Strip the `node_modules/` prefix during extraction.

---

## 8) Duplicating the same CVE for the same installed package/version
Mistake:
Including duplicate rows because multiple scanner results point to the same issue.

Why it fails:
Ground truth often expects unique package/version/CVE combinations.

Avoid it:
Deduplicate on `(Package, Version, CVE_ID)`.

---

# Fast Triage Checklist

Before submitting, confirm:

- [ ] `package-lock.json` was parsed correctly
- [ ] actual installed package versions were extracted
- [ ] only HIGH/CRITICAL findings remain
- [ ] every row maps to a real installed package/version
- [ ] CSV headers exactly match:
      `Package,Version,CVE_ID,Severity,CVSS_Score,Fixed_Version,Title,Url`
- [ ] rows are deduplicated
- [ ] fixed version uses `N/A` only when necessary
- [ ] CSV re-parses successfully

---

# Minimal Command Sequence

If you need a compact operational flow:

```bash
python - <<'PY'
import json
lock = json.load(open('/root/package-lock.json'))
print('lockfileVersion=', lock.get('lockfileVersion'))
print('has_packages=', isinstance(lock.get('packages'), dict))
PY

trivy fs --quiet --skip-db-update --scanners vuln --pkg-types library --format json --output /tmp/trivy.json /root

python /tmp/audit_package_lock.py
```

---

This skill is optimized for tasks where correctness depends equally on:

1. choosing the right dependency inventory source,
2. restricting to HIGH/CRITICAL advisories,
3. and producing a validator-exact CSV.