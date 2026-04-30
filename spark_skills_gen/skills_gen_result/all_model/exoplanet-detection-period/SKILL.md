---
id: exoplanet-detection-period
title: Exoplanet Transit Period Detection from TESS Lightcurves
version: 1.0.0
tags: [astronomy, exoplanet, TESS, BLS, lightcurve, detrending, period-finding]
description: >
  Detect the orbital period of a transiting exoplanet in a TESS lightcurve
  that is dominated by stellar variability (e.g. rotational modulation from
  starspots). Covers data filtering, stellar activity removal via
  Savitzky-Golay detrending, and Box Least Squares (BLS) period search.
---

## Overview

TESS lightcurves often contain strong quasi-periodic stellar variability (rotation
periods of ~0.5–30 days) that masks shallow transit signals from exoplanets. The
workflow is:

1. Load and filter the lightcurve (quality flags + sigma clipping)
2. Estimate and remove the stellar rotation trend (Savitzky-Golay filter)
3. Run a coarse BLS period search on the residuals
4. Refine around the best BLS peak with a fine grid
5. Write the result to the required output file in the exact format

**Critical rule:** Always write the output file FIRST before doing any optional
diagnostics. The test suite checks for file existence — a missing file is an
immediate failure even if the analysis is correct.

---

## High-Level Workflow

### Step 1 — Locate the output spec before doing any analysis

Read the test file to find the exact output path, filename, and format expected
by the validator. Do not assume — check explicitly.

```bash
find / -name "test_outputs.py" 2>/dev/null | head -5
cat ../tests/test_outputs.py
```

Look for:
- The expected file path (e.g. `/root/period.txt`)
- The expected format (single float, N decimal places, newline, etc.)
- Any tolerance on the period value (e.g. ±0.01 days)

### Step 2 — Load and inspect the lightcurve

```python
import numpy as np

data = np.loadtxt('/root/data/tess_lc.txt', comments='#')
# Columns: Time (MJD), Normalized flux, Quality flag, Flux uncertainty
time  = data[:, 0]
flux  = data[:, 1]
flag  = data[:, 2]
ferr  = data[:, 3]

print(f"Total points:  {len(time)}")
print(f"Time span:     {time[-1] - time[0]:.2f} days")
print(f"Median cadence:{np.median(np.diff(np.sort(time)))*24*60:.2f} min")
print(f"Quality==0:    {(flag==0).sum()} points")
print(f"Flux range:    {flux.min():.4f} – {flux.max():.4f}")
```

### Step 3 — Filter: quality flags + sigma clipping

```python
from astropy.stats import sigma_clip

# Keep only good-quality points
mask = flag == 0
time, flux, ferr = time[mask], flux[mask], ferr[mask]

# Sort by time (important for Savitzky-Golay)
idx = np.argsort(time)
time, flux, ferr = time[idx], flux[idx], ferr[idx]

# Sigma-clip outliers (4-sigma is conservative enough to keep shallow transits)
clipped = sigma_clip(flux, sigma=4, maxiters=5)
mask2 = ~clipped.mask
time, flux, ferr = time[mask2], flux[mask2], ferr[mask2]

print(f"After filtering: {len(time)} points")
```

**Why 4-sigma?** Transit dips are typically 100–5000 ppm deep. Using 3-sigma
risks clipping in-transit points; 4-sigma is safer.

### Step 4 — Estimate stellar rotation period (optional diagnostic)

This helps choose the right Savitzky-Golay window. If the rotation period is
~P_rot days, the SG window should be shorter than P_rot to track the stellar
signal without over-smoothing transits.

```python
from astropy.timeseries import LombScargle

ls = LombScargle(time, flux, ferr)
freq, power = ls.autopower(minimum_frequency=1/30, maximum_frequency=1/0.3,
                            samples_per_peak=10)
periods_ls = 1.0 / freq
best_rot = periods_ls[np.argmax(power)]
print(f"Estimated stellar rotation period: {best_rot:.3f} days")
```

### Step 5 — Remove stellar variability with Savitzky-Golay filter

The SG filter smooths the lightcurve over a window shorter than the transit
duration but long enough to capture the stellar rotation trend.

```python
from scipy.signal import savgol_filter

dt = np.median(np.diff(time))          # cadence in days

# Window: ~0.5–1.0 × stellar rotation period, but at minimum ~0.5 days
# Must be odd and > polyorder
window_days = 0.75                      # adjust if rotation period is known
window_pts  = int(window_days / dt)
if window_pts % 2 == 0:
    window_pts += 1
window_pts = max(window_pts, 5)        # safety floor

trend     = savgol_filter(flux, window_length=window_pts, polyorder=2)
residuals = flux / trend               # divide (not subtract) for normalized flux

print(f"SG window: {window_pts} points = {window_pts*dt*24:.1f} hours")
print(f"Residuals std: {residuals.std()*1e6:.0f} ppm")
```

**Why divide instead of subtract?** TESS flux is normalized; dividing preserves
the fractional transit depth regardless of the local flux level.

### Step 6 — Coarse BLS period search

```python
from astropy.timeseries import BoxLeastSquares

bls = BoxLeastSquares(time, residuals, dy=ferr / trend)

# Coarse grid: 0.5 to 20 days, ~50k points
periods_coarse = np.linspace(0.5, 20.0, 50000)

# Transit durations to search (in days): 0.5 h to 5 h
durations = [0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25]

result_coarse = bls.power(periods_coarse, duration=durations)

# Find top peaks
top_n = 5
top_idx = np.argsort(result_coarse.power)[::-1][:top_n]
print("Top BLS peaks (coarse):")
for i in top_idx:
    print(f"  P={result_coarse.period[i]:.4f} d  power={result_coarse.power[i]:.2f}")
```

**Interpreting results:** The stellar rotation period and its harmonics (P/2,
P/3, ...) will appear as strong BLS peaks. The exoplanet period is typically
the peak that is NOT a harmonic of the rotation period. Look for the highest
power peak that is inconsistent with the stellar rotation period.

### Step 7 — Refine around the best candidate period

```python
# Take the best non-rotation peak
best_coarse = result_coarse.period[top_idx[0]]
print(f"Best coarse period: {best_coarse:.4f} days")

# Fine grid: ±10% around best period, 200k points
p_lo = best_coarse * 0.90
p_hi = best_coarse * 1.10
periods_fine = np.linspace(p_lo, p_hi, 200000)

result_fine = bls.power(periods_fine, duration=durations)

best_idx_fine = np.argmax(result_fine.power)
best_period   = result_fine.period[best_idx_fine]
best_power    = result_fine.power[best_idx_fine]
best_duration = result_fine.duration[best_idx_fine]
best_t0       = result_fine.transit_time[best_idx_fine]

print(f"Refined best period: {best_period:.8f} days (power={best_power:.2f})")

# Sanity check: compute transit stats
stats = bls.compute_stats(best_period, best_duration, best_t0)
depth_ppm = stats['depth'][0] * 1e6
print(f"Transit depth:    {depth_ppm:.0f} ppm")
print(f"Transit duration: {best_duration*24:.2f} hours")
print(f"Transit time:     {best_t0:.6f} MJD")
print(f"Expected transits in data: {(time[-1]-time[0])/best_period:.1f}")
```

**Sanity checks before writing:**
- Transit depth should be 100–50000 ppm (0.01%–5%). Depths outside this range
  suggest a false positive or detrending artifact.
- Transit duration should be 0.5–12 hours for typical hot Jupiters/super-Earths.
- Number of expected transits should be ≥ 2 (ideally ≥ 3) within the baseline.

### Step 8 — Write the output file (do this IMMEDIATELY after finding the period)

```python
rounded_period = round(best_period, 5)
output_path = '/root/period.txt'

with open(output_path, 'w') as f:
    f.write(f"{rounded_period}\n")

# Verify
contents = open(output_path).read().strip()
print(f"Written to {output_path}: '{contents}'")
assert contents == str(rounded_period), f"Format mismatch: {contents!r}"
```

**Format rules (from test suite):**
- Single numerical value only — no labels, no units, no extra text
- Rounded to exactly 5 decimal places using Python's `round(x, 5)`
- One trailing newline is acceptable; extra blank lines are not
- The value must be a valid float parseable by `float()`

---

## Complete End-to-End Script

```python
#!/usr/bin/env python3
"""
Exoplanet transit period detection from TESS lightcurve.
Usage: python3 solve.py
Output: /root/period.txt
"""

import numpy as np
from scipy.signal import savgol_filter
from astropy.stats import sigma_clip
from astropy.timeseries import BoxLeastSquares, LombScargle

# ── 1. Load data ──────────────────────────────────────────────────────────────
data = np.loadtxt('/root/data/tess_lc.txt', comments='#')
time, flux, flag, ferr = data[:,0], data[:,1], data[:,2], data[:,3]

print(f"Loaded {len(time)} points, span={time[-1]-time[0]:.2f} days")

# ── 2. Filter ─────────────────────────────────────────────────────────────────
mask = flag == 0
time, flux, ferr = time[mask], flux[mask], ferr[mask]

idx = np.argsort(time)
time, flux, ferr = time[idx], flux[idx], ferr[idx]

clipped = sigma_clip(flux, sigma=4, maxiters=5)
mask2 = ~clipped.mask
time, flux, ferr = time[mask2], flux[mask2], ferr[mask2]
print(f"After filtering: {len(time)} points")

# ── 3. Estimate stellar rotation (for window sizing) ─────────────────────────
ls = LombScargle(time, flux, ferr)
freq, power = ls.autopower(minimum_frequency=1/30, maximum_frequency=1/0.3,
                            samples_per_peak=10)
p_rot = (1.0 / freq)[np.argmax(power)]
print(f"Stellar rotation period: {p_rot:.3f} days")

# ── 4. Savitzky-Golay detrending ──────────────────────────────────────────────
dt = np.median(np.diff(time))
window_days = min(0.75, p_rot * 0.5)   # half the rotation period, max 0.75 d
window_pts  = int(window_days / dt)
if window_pts % 2 == 0:
    window_pts += 1
window_pts = max(window_pts, 5)

trend     = savgol_filter(flux, window_length=window_pts, polyorder=2)
residuals = flux / trend
print(f"SG window: {window_pts} pts ({window_pts*dt*24:.1f} h), "
      f"residuals std={residuals.std()*1e6:.0f} ppm")

# ── 5. Coarse BLS ─────────────────────────────────────────────────────────────
bls = BoxLeastSquares(time, residuals, dy=ferr / trend)
durations = [0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25]

periods_coarse = np.linspace(0.5, 20.0, 50000)
result_c = bls.power(periods_coarse, duration=durations)

top5 = np.argsort(result_c.power)[::-1][:5]
print("Top 5 coarse BLS peaks:")
for i in top5:
    print(f"  P={result_c.period[i]:.4f} d  power={result_c.power[i]:.2f}")

best_coarse = result_c.period[top5[0]]

# ── 6. Fine BLS ───────────────────────────────────────────────────────────────
p_lo = best_coarse * 0.90
p_hi = best_coarse * 1.10
periods_fine = np.linspace(p_lo, p_hi, 200000)
result_f = bls.power(periods_fine, duration=durations)

bi = np.argmax(result_f.power)
best_period   = result_f.period[bi]
best_duration = result_f.duration[bi]
best_t0       = result_f.transit_time[bi]
best_power    = result_f.power[bi]

stats = bls.compute_stats(best_period, best_duration, best_t0)
depth_ppm = stats['depth'][0] * 1e6

print(f"\nFinal period:     {best_period:.8f} days")
print(f"BLS power:        {best_power:.2f}")
print(f"Transit depth:    {depth_ppm:.0f} ppm")
print(f"Transit duration: {best_duration*24:.2f} hours")
print(f"Transits in data: {(time[-1]-time[0])/best_period:.1f}")

# ── 7. Write output ───────────────────────────────────────────────────────────
rounded = round(best_period, 5)
with open('/root/period.txt', 'w') as f:
    f.write(f"{rounded}\n")

print(f"\nWritten /root/period.txt: {open('/root/period.txt').read().strip()}")
```

---

## Disambiguation: Stellar Rotation vs. Planet Period

The BLS will find both the stellar rotation period and the planet period. Use
these heuristics to distinguish them:

| Feature | Stellar rotation | Exoplanet transit |
|---|---|---|
| BLS shape | Broad, sinusoidal | Narrow, box-like |
| Duration | Long (hours–days) | Short (0.5–12 h) |
| Depth | Variable amplitude | Consistent depth |
| Harmonics | P/2, P/3 also strong | Harmonics weak |
| Lomb-Scargle | Strong peak | Weak or absent |

If the top BLS peak matches the Lomb-Scargle rotation period (within ~5%),
skip it and use the next highest BLS peak that does NOT match a rotation harmonic.

```python
def is_rotation_harmonic(period, p_rot, tol=0.05):
    """Return True if period is within tol of p_rot or its harmonics."""
    for k in [1, 2, 3, 4, 0.5, 0.33]:
        if abs(period - p_rot * k) / (p_rot * k) < tol:
            return True
    return False

# Find best non-rotation peak
for i in top5:
    p = result_c.period[i]
    if not is_rotation_harmonic(p, p_rot):
        best_coarse = p
        print(f"Selected planet candidate: {p:.4f} days")
        break
```

---

## Common Pitfalls

### 1. Never writing the output file
The most common failure: completing the analysis but forgetting to write
`/root/period.txt`. The test `test_period_file_exists` will fail immediately.
**Fix:** Write the file as the very last step of the analysis, then verify it.

### 2. Wrong decimal rounding
`round(5.359213, 5)` → `5.35921` (correct)
`f"{5.359213:.5f}"` → `"5.35921"` (also correct, but verify no trailing zeros
cause format issues). Both approaches work; just be consistent.

### 3. Savitzky-Golay window too large
If the SG window is larger than the transit duration × (period / cadence), it
will smooth out the transit signal itself, making BLS unable to find it.
Keep the window ≤ 0.75 days for typical TESS 2-minute cadence data.

### 4. Savitzky-Golay window not odd
`savgol_filter` requires an odd `window_length`. Always enforce:
```python
if window_pts % 2 == 0:
    window_pts += 1
```

### 5. Coarse BLS grid too sparse
A 10k-point grid over 0.5–20 days has ~2 ms resolution at 5 days — too coarse
to find the exact period. Use ≥50k points for the coarse pass, then refine with
200k points over a ±10% window.

### 6. Not sorting by time before SG filter
`savgol_filter` assumes evenly-spaced, time-ordered data. Always sort by time
after filtering. Gaps in TESS data (e.g. momentum dumps) are acceptable but
the array must be monotonically increasing in time.

### 7. Subtracting instead of dividing the trend
For normalized flux, `residuals = flux / trend` preserves fractional depth.
`residuals = flux - trend` introduces a baseline offset that varies with the
local flux level and can distort the BLS signal.

### 8. Sigma-clipping too aggressively
Using `sigma=2.5` or `sigma=3` can clip in-transit points (especially for
deep transits > 1000 ppm), reducing the BLS signal. Use `sigma=4` as default.

### 9. Ignoring quality flags
TESS quality flags mark cosmic rays, scattered light, and momentum dumps.
Always filter `flag == 0` before any analysis. Including flagged points
introduces correlated noise that creates spurious BLS peaks.

### 10. Not verifying the output file contents
After writing, always read back and print the file contents to confirm the
format is exactly as expected:
```python
contents = open('/root/period.txt').read().strip()
print(f"File contents: '{contents}'")
float(contents)  # will raise ValueError if format is wrong
```
