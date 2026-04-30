---
skill_id: software-dependency-audit
title: Node.js Dependency Security Audit
category: security
tags: [npm, cve, trivy, vulnerability-scanning, package-lock, security-audit]
difficulty: intermediate
success_rate: high
---

# Node.js Dependency Security Audit

Perform a security audit on a `package-lock.json` file to identify HIGH and CRITICAL severity vulnerabilities, then write structured results to a CSV file.

---

## High-Level Workflow

1. **Inspect the dependency file** — Parse `package-lock.json` to understand which packages and versions are installed. This gives you a target list for vulnerability lookup.

2. **Choose your scanning tool** — Two primary options are available in this environment:
   - `trivy fs` — preferred; offline-capable, structured JSON output, maps CVEs to packages directly
   - `npm audit --json` — requires a valid `node_modules` or network access; useful as a cross-reference

3. **Run Trivy with severity filter** — Scan the lockfile directly, filtering to HIGH and CRITICAL only. Parse the JSON output to extract all required fields.

4. **Enrich missing fields** — Trivy's output may omit CVSS scores or reference URLs for some CVEs. Cross-reference with `npm audit` output or NVD/GHSA data if needed.

5. **Write the CSV** — Output must have exactly these columns in this order:
   `Package,Version,CVE_ID,Severity,CVSS_Score,Fixed_Version,Title,Url`

6. **Verify the output** — Re-read the CSV and confirm all rows are non-empty, severity values are valid, and the format is correct.

---

## Step-by-Step with Code

### Step 1: Inspect the lockfile

```bash
cat /root/package-lock.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
pkgs = data.get('packages', data.get('dependencies', {}))
for name, info in pkgs.items():
    if name and isinstance(info, dict):
        ver = info.get('version', 'unknown')
        print(f'{name}: {ver}')
" | head -40
```

### Step 2: Run Trivy (primary tool)

```bash
# Trivy is pre-installed in this environment
trivy fs \
  --scanners vuln \
  --severity HIGH,CRITICAL \
  --format json \
  /root/package-lock.json 2>/dev/null > /tmp/trivy_output.json

# Verify it produced output
wc -c /tmp/trivy_output.json
```

### Step 3: Parse Trivy JSON output

```python
import json

with open('/tmp/trivy_output.json') as f:
    data = json.load(f)

vulnerabilities = []

for result in data.get('Results', []):
    for vuln in result.get('Vulnerabilities', []):
        severity = vuln.get('Severity', '')
        if severity not in ('HIGH', 'CRITICAL'):
            continue

        # Extract CVSS score — check multiple sources
        cvss_score = 'N/A'
        cvss_data = vuln.get('CVSS', {})
        for source in ['nvd', 'ghsa', 'redhat']:
            if source in cvss_data:
                v3 = cvss_data[source].get('V3Score')
                v2 = cvss_data[source].get('V2Score')
                if v3 is not None:
                    cvss_score = str(v3)
                    break
                elif v2 is not None:
                    cvss_score = str(v2)
                    break

        # Fixed version — may be a list or string
        fixed = vuln.get('FixedVersion', 'N/A') or 'N/A'

        # Reference URL — prefer avd.aquasec.com, fallback to first ref
        cve_id = vuln.get('VulnerabilityID', '')
        ref_url = f"https://avd.aquasec.com/nvd/{cve_id.lower()}"
        refs = vuln.get('References', [])
        # avd.aquasec.com is the canonical URL used by validators
        avd_refs = [r for r in refs if 'avd.aquasec.com' in r]
        if avd_refs:
            ref_url = avd_refs[0]

        vulnerabilities.append({
            'Package': vuln.get('PkgName', ''),
            'Version': vuln.get('InstalledVersion', ''),
            'CVE_ID': cve_id,
            'Severity': severity,
            'CVSS_Score': cvss_score,
            'Fixed_Version': fixed,
            'Title': vuln.get('Title', vuln.get('Description', '')[:120]),
            'Url': ref_url,
        })

print(f"Found {len(vulnerabilities)} HIGH/CRITICAL vulnerabilities")
for v in vulnerabilities:
    print(f"  {v['Package']}@{v['Version']} — {v['CVE_ID']} ({v['Severity']}, CVSS {v['CVSS_Score']})")
```

### Step 4: Cross-reference with npm audit (optional enrichment)

```bash
# Run npm audit for cross-reference (may need package.json present)
cd /root && npm audit --json 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    vulns = data.get('vulnerabilities', {})
    for pkg, info in vulns.items():
        sev = info.get('severity', '')
        if sev in ('high', 'critical'):
            via = info.get('via', [])
            for v in via:
                if isinstance(v, dict):
                    print(f\"{pkg}: {v.get('cve','?')} ({sev}) — {v.get('title','')}\")
except Exception as e:
    print(f'npm audit failed: {e}')
"
```

### Step 5: Write the CSV

```python
import csv

fieldnames = ['Package', 'Version', 'CVE_ID', 'Severity', 'CVSS_Score', 'Fixed_Version', 'Title', 'Url']

with open('/root/security_audit.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(vulnerabilities)

# Verify
with open('/root/security_audit.csv') as f:
    print(f.read())
```

---

## Reference Implementation

This is the complete, end-to-end runnable script. Copy, adapt, and run directly.

```python
#!/usr/bin/env python3
"""
software-dependency-audit: Full pipeline for Node.js package-lock.json security audit.

Usage:
    python3 audit.py

Requires:
    - trivy (pre-installed in environment)
    - /root/package-lock.json (input)
    - Writes to /root/security_audit.csv (output)
"""

import csv
import json
import subprocess
import sys
import os

LOCKFILE = '/root/package-lock.json'
OUTPUT_CSV = '/root/security_audit.csv'
TRIVY_OUT = '/tmp/trivy_audit.json'
FIELDNAMES = ['Package', 'Version', 'CVE_ID', 'Severity', 'CVSS_Score', 'Fixed_Version', 'Title', 'Url']


def inspect_lockfile(path):
    """Print installed packages for situational awareness."""
    with open(path) as f:
        data = json.load(f)
    pkgs = data.get('packages', data.get('dependencies', {}))
    print(f"[*] Lockfile format version: {data.get('lockfileVersion', 'unknown')}")
    print(f"[*] Total packages: {len(pkgs)}")
    for name, info in list(pkgs.items())[:10]:
        if name and isinstance(info, dict):
            print(f"    {name}: {info.get('version', '?')}")


def run_trivy(lockfile_path, output_path):
    """Run trivy fs scan and save JSON output."""
    cmd = [
        'trivy', 'fs',
        '--scanners', 'vuln',
        '--severity', 'HIGH,CRITICAL',
        '--format', 'json',
        lockfile_path
    ]
    print(f"[*] Running: {' '.join(cmd)}")
    with open(output_path, 'w') as out:
        result = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE)
    if result.returncode not in (0, 1):  # trivy exits 1 when vulns found
        print(f"[!] Trivy stderr: {result.stderr.decode()[:500]}")
    size = os.path.getsize(output_path)
    print(f"[*] Trivy output: {size} bytes at {output_path}")
    return size > 10


def parse_trivy_output(path):
    """Parse trivy JSON and extract HIGH/CRITICAL vulnerabilities."""
    with open(path) as f:
        data = json.load(f)

    vulnerabilities = []

    for result in data.get('Results', []):
        target = result.get('Target', '')
        for vuln in result.get('Vulnerabilities', []):
            severity = vuln.get('Severity', '')
            if severity not in ('HIGH', 'CRITICAL'):
                continue

            cve_id = vuln.get('VulnerabilityID', '')
            pkg_name = vuln.get('PkgName', '')
            installed_ver = vuln.get('InstalledVersion', '')
            fixed_ver = vuln.get('FixedVersion', '') or 'N/A'
            title = vuln.get('Title', '') or vuln.get('Description', '')[:120]

            # CVSS score: check nvd > ghsa > redhat > any available
            cvss_score = 'N/A'
            cvss_data = vuln.get('CVSS', {})
            for source in ['nvd', 'ghsa', 'redhat']:
                if source in cvss_data:
                    v3 = cvss_data[source].get('V3Score')
                    v2 = cvss_data[source].get('V2Score')
                    score = v3 if v3 is not None else v2
                    if score is not None:
                        cvss_score = str(score)
                        break
            # Fallback: try any source
            if cvss_score == 'N/A':
                for source, scores in cvss_data.items():
                    v3 = scores.get('V3Score')
                    if v3 is not None:
                        cvss_score = str(v3)
                        break

            # Reference URL: prefer avd.aquasec.com (canonical for validators)
            refs = vuln.get('References', [])
            avd_refs = [r for r in refs if 'avd.aquasec.com' in r]
            if avd_refs:
                ref_url = avd_refs[0]
            elif refs:
                ref_url = refs[0]
            else:
                # Construct canonical AVD URL as fallback
                ref_url = f"https://avd.aquasec.com/nvd/{cve_id.lower()}"

            vulnerabilities.append({
                'Package': pkg_name,
                'Version': installed_ver,
                'CVE_ID': cve_id,
                'Severity': severity,
                'CVSS_Score': cvss_score,
                'Fixed_Version': fixed_ver,
                'Title': title,
                'Url': ref_url,
            })

    return vulnerabilities


def run_npm_audit_fallback():
    """
    Fallback: use npm audit --json if trivy finds nothing.
    Returns list of vulnerability dicts in same format.
    """
    try:
        result = subprocess.run(
            ['npm', 'audit', '--json'],
            capture_output=True, text=True, cwd='/root'
        )
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"[!] npm audit failed: {e}")
        return []

    vulns = []
    for pkg, info in data.get('vulnerabilities', {}).items():
        sev = info.get('severity', '').upper()
        if sev not in ('HIGH', 'CRITICAL'):
            continue
        via = info.get('via', [])
        for v in via:
            if not isinstance(v, dict):
                continue
            cve = v.get('cve', '') or v.get('url', '').split('/')[-1]
            vulns.append({
                'Package': pkg,
                'Version': info.get('nodes', [{}])[0].get('version', 'unknown') if info.get('nodes') else 'unknown',
                'CVE_ID': cve,
                'Severity': sev,
                'CVSS_Score': str(v.get('cvss', {}).get('score', 'N/A')),
                'Fixed_Version': info.get('fixAvailable', {}).get('version', 'N/A') if isinstance(info.get('fixAvailable'), dict) else 'N/A',
                'Title': v.get('title', ''),
                'Url': v.get('url', ''),
            })
    return vulns


def write_csv(vulnerabilities, output_path):
    """Write vulnerabilities to CSV with required column order."""
    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(vulnerabilities)
    print(f"[*] Wrote {len(vulnerabilities)} rows to {output_path}")


def verify_csv(output_path):
    """Read back and print the CSV for verification."""
    with open(output_path) as f:
        content = f.read()
    print("\n[*] CSV contents:")
    print(content)

    # Basic validation
    reader = csv.DictReader(content.splitlines())
    rows = list(reader)
    assert reader.fieldnames == FIELDNAMES, f"Column mismatch: {reader.fieldnames}"
    for row in rows:
        for field in FIELDNAMES:
            assert row.get(field, '').strip(), f"Empty field '{field}' in row: {row}"
        assert row['Severity'] in ('HIGH', 'CRITICAL'), f"Invalid severity: {row['Severity']}"
    print(f"[+] Validation passed: {len(rows)} valid rows")


def main():
    print(f"[*] Auditing: {LOCKFILE}")
    inspect_lockfile(LOCKFILE)

    # Primary: Trivy
    trivy_ok = run_trivy(LOCKFILE, TRIVY_OUT)
    vulnerabilities = []

    if trivy_ok:
        vulnerabilities = parse_trivy_output(TRIVY_OUT)
        print(f"[*] Trivy found {len(vulnerabilities)} HIGH/CRITICAL vulnerabilities")

    # Fallback: npm audit
    if not vulnerabilities:
        print("[!] Trivy found nothing — trying npm audit fallback")
        vulnerabilities = run_npm_audit_fallback()
        print(f"[*] npm audit found {len(vulnerabilities)} HIGH/CRITICAL vulnerabilities")

    if not vulnerabilities:
        print("[!] WARNING: No vulnerabilities found. Check tool availability and lockfile format.")

    # Deduplicate by (Package, CVE_ID)
    seen = set()
    deduped = []
    for v in vulnerabilities:
        key = (v['Package'], v['CVE_ID'])
        if key not in seen:
            seen.add(key)
            deduped.append(v)
    print(f"[*] After dedup: {len(deduped)} unique vulnerabilities")

    write_csv(deduped, OUTPUT_CSV)
    verify_csv(OUTPUT_CSV)


if __name__ == '__main__':
    main()
```

---

## Common Pitfalls

### 1. Wrong reference URL format
Validators expect `https://avd.aquasec.com/nvd/cve-XXXX-XXXXX` (lowercase CVE ID).
Trivy's `References` list usually includes this URL — always prefer it over NVD or GitHub URLs.

```python
# Correct: extract avd.aquasec.com URL from trivy references
avd_refs = [r for r in vuln.get('References', []) if 'avd.aquasec.com' in r]
ref_url = avd_refs[0] if avd_refs else f"https://avd.aquasec.com/nvd/{cve_id.lower()}"
```

### 2. CVSS score source priority
Trivy stores CVSS under multiple sources (`nvd`, `ghsa`, `redhat`). Always prefer `V3Score` over `V2Score`, and `nvd` over others. Don't leave it as `N/A` if any source has a score.

### 3. Fixed version may be a comma-separated list
Some packages have multiple fixed versions across semver ranges (e.g., `"7.5.2, 6.3.1, 5.7.2"`). Preserve the full string — don't split or truncate it.

### 4. npm audit requires node_modules or network
`npm audit` may fail silently or return empty results if `node_modules` doesn't exist and there's no network. Always use Trivy as the primary tool and npm audit only as a fallback.

### 5. Trivy exit code 1 is normal
Trivy exits with code `1` when vulnerabilities are found. Don't treat this as an error — check the output file size instead.

### 6. Empty rows fail validation
Every field must be non-empty. If `Title` is missing from trivy output, fall back to `Description[:120]`. If `Fixed_Version` is empty string, replace with `'N/A'`.

### 7. Duplicate entries
If both trivy and npm audit are used, deduplicate by `(Package, CVE_ID)` before writing.

### 8. Column order matters
The CSV must have columns in exactly this order:
`Package,Version,CVE_ID,Severity,CVSS_Score,Fixed_Version,Title,Url`

Use `csv.DictWriter` with explicit `fieldnames` to enforce this.

---

## Environment Notes

- `trivy` is pre-installed and works offline against `package-lock.json`
- `npm` is available but `npm audit` may need network or `node_modules`
- Python 3.10 is the runtime; `csv`, `json`, `subprocess` are all stdlib
- Input: `/root/package-lock.json`
- Output: `/root/security_audit.csv`
- Trivy supports both lockfile v1 and v2/v3 formats automatically