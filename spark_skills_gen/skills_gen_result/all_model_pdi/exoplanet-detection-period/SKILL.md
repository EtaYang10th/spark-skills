---
title: "Exoplanet Transit Period Detection from TESS Lightcurves"
category: exoplanet-detection-period
domain: astrophysics
tags:
  - TESS
  - lightcurve
  - exoplanet
  - transit
  - BLS
  - detrending
  - stellar-variability
  - time-series
tools:
  - numpy
  - scipy
  - astropy
  - wotan
  - matplotlib
date: 2025-01-01
---

# Exoplanet Transit Period Detection from TESS Lightcurves

## Overview

TESS (Transiting Exoplanet Survey Satellite) lightcurves often contain strong stellar variability (starspot rotational modulation) that masks shallow exoplanet transit signals. The core challenge is **separating the stellar signal from the planetary signal** so that a Box Least Squares (BLS) periodogram can recover the true orbital period.

This skill covers the full pipeline: data loading → quality filtering → stellar variability removal → BLS period search → iterative refinement → validation via phase-folding.

---

## High-Level Workflow

### Step 1: Load and Inspect the Data

**Why:** Understand the time baseline, cadence, flux distribution, and quality flags before processing. The time baseline constrains the longest detectable period; the cadence constrains the shortest detectable transit duration.

```python
import numpy as np

data = np.loadtxt('/root/data/tess_lc.txt')
time, flux, flag, flux_err = data[:, 0], data[:, 1], data[:, 2], data[:, 3]

print(f"Points: {len(time)}")
print(f"Time range: {time.min():.2f} – {time.max():.2f} MJD  (span={time.ptp():.2f} d)")
print(f"Unique quality flags: {np.unique(flag)}")
print(f"Flux range: {flux.min():.6f} – {flux.max():.6f}")
print(f"Median cadence: {np.median(np.diff(time))*24*60:.1f} min")
```

**Decision point:** If the time span is ~27 days (one TESS sector), periods up to ~13 days are reliably detectable. Shorter baselines reduce sensitivity to longer periods.

### Step 2: Filter Bad Data

**Why:** Quality flags, NaN values, and extreme outliers corrupt periodograms and detrending models.

```python
from astropy.stats import sigma_clip

# Quality and NaN filter
good = (flag == 0) & np.isfinite(flux) & np.isfinite(time) & np.isfinite(flux_err)
time, flux, flux_err = time[good], flux[good], flux_err[good]

# Sigma-clip extreme outliers (use generous 5-sigma to preserve transits)
clipped = sigma_clip(flux, sigma=5, maxiters=3)
mask = ~clipped.mask
time, flux, flux_err = time[mask], flux[mask], flux_err[mask]
print(f"After filtering: {len(time)} points")
```

**Critical note:** Use sigma ≥ 5 for the initial clip. Transits are dips of 0.1–1%, and aggressive clipping (3-sigma) can remove transit points, destroying the signal you're trying to find.

### Step 3: Identify and Remove Stellar Variability

**Why:** Stellar rotation (typically 0.5–15 days for active stars) produces quasi-sinusoidal modulation that dominates the lightcurve. BLS will lock onto this signal or its harmonics instead of the transit if it's not removed.

**Strategy selection:**

| Method | When to use | Pros | Cons |
|--------|------------|------|------|
| Fourier series (10+ harmonics) | Strong periodic stellar signal | Excellent at removing coherent oscillations; preserves transit shape | Needs good initial frequency estimate |
| Wotan biweight/median filter | Smooth, slow trends | Simple, robust | Can distort transits if window too short |
| Gaussian Process | Complex, non-periodic variability | Flexible | Slow on large datasets; can overfit transits |
| Combined Fourier + biweight | Strong rotation + residual trends | Best overall for TESS data | Two-step process |

**Recommended approach: Fourier series + biweight (two-stage detrending)**

#### Stage A: Fourier series to remove stellar rotation

First, estimate the rotation period from a Lomb-Scargle periodogram:

```python
from astropy.timeseries import LombScargle

ls = LombScargle(time, flux, flux_err)
frequency, power = ls.autopower(minimum_frequency=0.1, maximum_frequency=5.0,
                                 samples_per_peak=10)
rotation_freq = frequency[np.argmax(power)]
rotation_period = 1.0 / rotation_freq
print(f"Stellar rotation period: {rotation_period:.3f} d (freq={rotation_freq:.4f} 1/d)")
```

Then fit a multi-harmonic Fourier model:

```python
from scipy.optimize import curve_fit

def fourier_model(t, *params):
    """Multi-harmonic Fourier series: params = [freq, offset, a1, b1, a2, b2, ...]"""
    f = params[0]
    offset = params[1]
    n_harmonics = (len(params) - 2) // 2
    result = np.full_like(t, offset)
    for n in range(n_harmonics):
        a = params[2 + 2 * n]
        b = params[3 + 2 * n]
        result += a * np.cos(2 * np.pi * (n + 1) * f * t) + b * np.sin(2 * np.pi * (n + 1) * f * t)
    return result

n_harm = 10  # 10 harmonics captures complex spot patterns
p0 = [rotation_freq, 1.0] + [0.001, 0.001] * n_harm
popt, _ = curve_fit(fourier_model, time, flux, p0=p0, maxfev=50000)
stellar_model = fourier_model(time, *popt)

# Divide out the stellar signal
detrended = flux / stellar_model
```

**Why 10 harmonics?** Starspot modulation is not purely sinusoidal. Sharp features (spot ingress/egress) require higher harmonics. Using too few (e.g., 2–3) leaves residual variability that BLS picks up as false transit signals.

#### Stage B: Biweight filter for residual trends

```python
from wotan import flatten

flat, trend = flatten(time, detrended, window_length=1.0, method='biweight', return_trend=True)
valid = np.isfinite(flat)
t_clean, f_clean, e_clean = time[valid], flat[valid], flux_err[valid]
```

**Window length selection:** Use 1.0 day for the biweight filter. This is long enough to preserve transit shapes (typical duration 1–4 hours) but short enough to remove residual trends. If the expected transit duration is very long (>0.2 d), increase to 1.5–2.0 days.

### Step 4: BLS Period Search (Coarse)

**Why:** Box Least Squares is the standard algorithm for detecting periodic box-shaped dips (transits) in time series.

```python
from astropy.timeseries import BoxLeastSquares

bls = BoxLeastSquares(t_clean, f_clean, dy=e_clean)

# Coarse search over a wide period range
# Minimum period: ~2× cadence; Maximum period: ~half the time baseline
min_period = 0.5
max_period = t_clean.ptp() / 2.0
coarse_periods = np.linspace(min_period, max_period, 50000)

# Use multiple transit durations to be sensitive to different planet sizes/orbits
durations = [0.03, 0.05, 0.08, 0.1, 0.12, 0.15]  # days
result = bls.power(coarse_periods, duration=durations)

power = np.array(result.power)
periods = np.array(result.period)

# Compute Signal Detection Efficiency (SDE)
sde = (power - np.mean(power)) / np.std(power)

# Find top peaks
top_indices = np.argsort(sde)[::-1][:10]
print("Top 10 BLS peaks:")
for i, idx in enumerate(top_indices):
    print(f"  {i+1}. P={periods[idx]:.5f} d  SDE={sde[idx]:.1f}  dur={result.duration[idx]:.3f} d")
```

### Step 5: Distinguish Planet Signal from Stellar Harmonics

**Why:** This is the most critical decision point. The BLS will often find the stellar rotation period or its harmonics (P_rot, P_rot/2, 2×P_rot) as the strongest peaks. The true planet signal may be the 2nd, 3rd, or even 5th strongest peak.

**Decision criteria:**

1. **Compare with rotation period:** If the top BLS peak is within 10% of P_rot, P_rot/2, or 2×P_rot, it's likely a stellar artifact. Skip it.
2. **Check transit duration:** Real transits have durations of 0.5–4 hours (0.02–0.17 days). If the "best" duration is suspiciously long (>0.2 d), it's probably not a transit.
3. **Phase-fold and inspect:** A real transit produces a clean, sharp dip at one phase. Stellar residuals produce broad, sinusoidal-looking phase curves.
4. **Check SDE:** An SDE > 5 is a strong detection. SDE 3–5 is marginal but can be real if the transit is shallow.

```python
# Filter out peaks near stellar rotation harmonics
rotation_freq_fit = popt[0]
rotation_period_fit = 1.0 / rotation_freq_fit

planet_candidates = []
for idx in top_indices:
    p = periods[idx]
    # Check if this period is a harmonic of stellar rotation
    is_harmonic = False
    for mult in [0.5, 1.0, 2.0, 3.0, 4.0]:
        if abs(p - mult * rotation_period_fit) / (mult * rotation_period_fit) < 0.10:
            is_harmonic = True
            break
    if not is_harmonic and sde[idx] > 3.0:
        planet_candidates.append((p, sde[idx], result.duration[idx], result.transit_time[idx]))

if planet_candidates:
    best_p, best_sde, best_dur, best_t0 = planet_candidates[0]
    print(f"\nBest planet candidate: P={best_p:.5f} d, SDE={best_sde:.1f}")
else:
    # Fallback: take the strongest non-rotation peak regardless of SDE
    print("WARNING: No strong non-harmonic peak found. Using best overall peak.")
    best_p = periods[top_indices[0]]
```

### Step 6: Fine BLS Refinement

**Why:** The coarse grid has limited resolution. A fine grid around the candidate period recovers the precise period needed for 5-decimal-place accuracy.

```python
# Fine search: ±0.35 days around candidate, 200k points
fine_periods = np.linspace(best_p - 0.35, best_p + 0.35, 200000)
fine_result = bls.power(fine_periods, duration=durations)
fine_power = np.array(fine_result.power)
fine_periods_arr = np.array(fine_result.period)
fine_best_idx = np.argmax(fine_power)
refined_period = float(fine_periods_arr[fine_best_idx])
refined_duration = float(fine_result.duration[fine_best_idx])
refined_t0 = float(fine_result.transit_time[fine_best_idx])
print(f"Refined period: {refined_period:.6f} d")

# Ultra-fine: ±0.02 days, 200k points
ultra_periods = np.linspace(refined_period - 0.02, refined_period + 0.02, 200000)
uf_result = bls.power(ultra_periods, duration=refined_duration)
uf_power = np.array(uf_result.power)
uf_periods = np.array(uf_result.period)
uf_best = np.argmax(uf_power)
final_period = float(uf_periods[uf_best])
final_t0 = float(uf_result.transit_time[uf_best])
print(f"Final period: {final_period:.6f} d")
```

### Step 7: Validate via Phase-Folding

**Why:** Phase-folding at the correct period should produce a clear, sharp transit dip. This confirms the period is real and not an artifact.

```python
phase = ((t_clean - final_t0) % final_period) / final_period
phase[phase > 0.5] -= 1.0

# Bin the phase-folded lightcurve
nbins = 200
bin_edges = np.linspace(-0.5, 0.5, nbins + 1)
bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
bin_flux = []
for i in range(nbins):
    m = (phase >= bin_edges[i]) & (phase < bin_edges[i + 1])
    if m.sum() > 0:
        bin_flux.append(np.median(f_clean[m]))
    else:
        bin_flux.append(np.nan)
bin_flux = np.array(bin_flux)

transit_depth = 1.0 - np.nanmin(bin_flux)
min_phase = bin_centers[np.nanargmin(bin_flux)]
print(f"Transit depth: {transit_depth * 100:.3f}%")
print(f"Phase of minimum: {min_phase:.3f}")
```

**Validation checks:**
- Transit depth should be 0.05–2% for typical exoplanets
- The dip should be localized (occupying <10% of the phase)
- The minimum should be near phase 0.0 (by construction from BLS t0)
- Out-of-transit flux should be flat and near 1.0

### Step 8: Write the Result

```python
with open('/root/period.txt', 'w') as f:
    f.write(f"{final_period:.5f}\n")
print(f"Written: {final_period:.5f}")
```

---

## Common Pitfalls

### 1. BLS Locks onto Stellar Rotation Period or Harmonics

**Symptom:** The strongest BLS peak is at ~P_rot or P_rot/2, not the planet period.

**Cause:** Incomplete removal of stellar variability. Even small residuals from a strong stellar signal dominate the BLS periodogram.

**Fix:** Use a high-order Fourier series (10 harmonics) for detrending, not just a simple median filter or low-order polynomial. Follow with a biweight filter for residual trends. Always compare BLS peaks against the known rotation period and its harmonics.

### 2. Aggressive Sigma-Clipping Removes Transit Points

**Symptom:** BLS finds no significant signal, or the transit depth is anomalously shallow.

**Cause:** Using sigma=3 clips the deepest transit points, which are the most informative.

**Fix:** Use sigma ≥ 5 for initial outlier rejection. Transit depths are typically 0.1–1%, well within 5-sigma for most lightcurves.

### 3. Detrending Window Too Short Distorts Transits

**Symptom:** Phase-folded lightcurve shows a broad, shallow dip instead of a sharp transit.

**Cause:** A biweight/median filter with window < 0.5 days partially removes the transit signal itself.

**Fix:** Use window_length ≥ 1.0 day for the biweight filter. Transits last 1–4 hours; a 1-day window is 6–24× longer and preserves the transit shape.

### 4. Coarse Period Grid Misses the True Period

**Symptom:** The period is off by >0.01 days from the true value.

**Cause:** Insufficient grid resolution in the BLS search.

**Fix:** Use a three-stage search: coarse (50k points over full range) → fine (200k points over ±0.35 d) → ultra-fine (200k points over ±0.02 d). This achieves sub-0.0001 day precision.

### 5. Not Using Multiple Transit Durations

**Symptom:** BLS misses the transit because the assumed duration is wrong.

**Cause:** Using a single duration value that doesn't match the actual transit.

**Fix:** Always search over multiple durations: `[0.03, 0.05, 0.08, 0.1, 0.12, 0.15]` days covers the range of typical TESS transits.

### 6. Gaussian Process Detrending is Too Slow or Overfits

**Symptom:** GP fitting takes >10 minutes or removes the transit signal.

**Cause:** GPs scale as O(N³) and can model transits as "variability" if the kernel is too flexible.

**Fix:** Prefer Fourier + biweight for TESS data. If you must use a GP, use a Matérn-3/2 kernel with a length scale fixed to >1 day, and mask known transit times during fitting.

### 7. Confusing Period Aliases

**Symptom:** BLS finds a period that is 1/2, 1/3, or 2× the true period.

**Cause:** Aliases are inherent in periodogram analysis, especially with gaps in the data.

**Fix:** For each candidate period P, also check P/2, P/3, 2P, and 3P. Phase-fold at each and visually/statistically compare the transit shape. The correct period produces the deepest, sharpest transit with the most consistent depth across epochs.

---

## Environment Setup

These packages are available in the standard TESS analysis environment:

```bash
# Already installed in the base image
# numpy, scipy, astropy, matplotlib, pandas, numba

# May need to install wotan for detrending
pip install wotan -q
```

Key imports:
```python
import numpy as np
from scipy.optimize import curve_fit
from astropy.timeseries import BoxLeastSquares, LombScargle
from astropy.stats import sigma_clip
from wotan import flatten
```

---

## Reference Implementation

This is a complete, end-to-end script. Copy, adapt the file path, and run.

```python
#!/usr/bin/env python3
"""
Exoplanet Transit Period Detection from TESS Lightcurve
=======================================================
Pipeline: Load → Filter → Fourier detrend (stellar rotation) → Biweight detrend
          (residuals) → Coarse BLS → Harmonic filtering → Fine BLS → Ultra-fine
          BLS → Phase-fold validation → Write result.

Input:  Lightcurve file with columns [Time(MJD), Normalized_flux, Quality_flag, Flux_err]
Output: period.txt with the orbital period rounded to 5 decimal places.
"""

import numpy as np
from scipy.optimize import curve_fit
from astropy.timeseries import BoxLeastSquares, LombScargle
from astropy.stats import sigma_clip

# Install wotan if not present (safe to call multiple times)
try:
    from wotan import flatten
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "wotan", "-q"])
    from wotan import flatten

# ============================================================
# CONFIG — adjust these for different datasets
# ============================================================
INPUT_FILE = '/root/data/tess_lc.txt'
OUTPUT_FILE = '/root/period.txt'
SIGMA_CLIP_THRESHOLD = 5       # sigma for outlier rejection (>=5 to preserve transits)
N_HARMONICS = 10               # Fourier harmonics for stellar rotation removal
BIWEIGHT_WINDOW = 1.0          # days, for residual trend removal
BLS_DURATIONS = [0.03, 0.05, 0.08, 0.1, 0.12, 0.15]  # transit duration grid (days)
COARSE_GRID_SIZE = 50000
FINE_GRID_SIZE = 200000
ULTRA_FINE_GRID_SIZE = 200000
HARMONIC_TOLERANCE = 0.10      # fractional tolerance for harmonic identification

# ============================================================
# STEP 1: Load and filter data
# ============================================================
print("=" * 60)
print("STEP 1: Loading and filtering data")
print("=" * 60)

data = np.loadtxt(INPUT_FILE)
time, flux, flag, flux_err = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
print(f"Raw data: {len(time)} points, time span: {time.ptp():.2f} days")

# Quality flag and NaN filter
good = (flag == 0) & np.isfinite(flux) & np.isfinite(time) & np.isfinite(flux_err)
time, flux, flux_err = time[good], flux[good], flux_err[good]
print(f"After quality filter: {len(time)} points")

# Sigma-clip outliers (generous threshold to preserve transits)
clipped = sigma_clip(flux, sigma=SIGMA_CLIP_THRESHOLD, maxiters=3)
mask = ~clipped.mask
time, flux, flux_err = time[mask], flux[mask], flux_err[mask]
print(f"After {SIGMA_CLIP_THRESHOLD}-sigma clip: {len(time)} points")

# ============================================================
# STEP 2: Identify stellar rotation period
# ============================================================
print("\n" + "=" * 60)
print("STEP 2: Identifying stellar rotation period")
print("=" * 60)

ls = LombScargle(time, flux, flux_err)
frequency, ls_power = ls.autopower(minimum_frequency=0.1, maximum_frequency=5.0,
                                    samples_per_peak=10)
rotation_freq = frequency[np.argmax(ls_power)]
rotation_period = 1.0 / rotation_freq
print(f"Stellar rotation period: {rotation_period:.4f} days")
print(f"Stellar rotation frequency: {rotation_freq:.4f} 1/d")

# ============================================================
# STEP 3: Remove stellar variability with Fourier series
# ============================================================
print("\n" + "=" * 60)
print("STEP 3: Fourier detrending (removing stellar rotation)")
print("=" * 60)

def fourier_model(t, *params):
    """Multi-harmonic Fourier series.
    params = [frequency, offset, a1, b1, a2, b2, ..., aN, bN]
    """
    f = params[0]
    offset = params[1]
    n_harmonics = (len(params) - 2) // 2
    result = np.full_like(t, offset)
    for n in range(n_harmonics):
        a = params[2 + 2 * n]
        b = params[3 + 2 * n]
        result += a * np.cos(2 * np.pi * (n + 1) * f * t) + \
                  b * np.sin(2 * np.pi * (n + 1) * f * t)
    return result

p0 = [rotation_freq, 1.0] + [0.001, 0.001] * N_HARMONICS
popt, pcov = curve_fit(fourier_model, time, flux, p0=p0, maxfev=50000)
stellar_model = fourier_model(time, *popt)
fitted_rotation_freq = popt[0]
fitted_rotation_period = 1.0 / fitted_rotation_freq

# Divide out the stellar signal
detrended = flux / stellar_model
print(f"Fitted rotation frequency: {fitted_rotation_freq:.4f} 1/d")
print(f"Fitted rotation period: {fitted_rotation_period:.4f} d")
print(f"Detrended flux std: {np.std(detrended):.6f}")

# ============================================================
# STEP 4: Secondary biweight detrend for residual trends
# ============================================================
print("\n" + "=" * 60)
print("STEP 4: Biweight detrending (residual trends)")
print("=" * 60)

flat, trend = flatten(time, detrended, window_length=BIWEIGHT_WINDOW,
                      method='biweight', return_trend=True)
valid = np.isfinite(flat)
t_clean = time[valid]
f_clean = flat[valid]
e_clean = flux_err[valid]
print(f"Clean data: {len(t_clean)} points, flux std: {np.std(f_clean):.6f}")

# ============================================================
# STEP 5: Coarse BLS period search
# ============================================================
print("\n" + "=" * 60)
print("STEP 5: Coarse BLS period search")
print("=" * 60)

bls = BoxLeastSquares(t_clean, f_clean, dy=e_clean)

min_period = 0.5
max_period = t_clean.ptp() / 2.0
coarse_periods = np.linspace(min_period, max_period, COARSE_GRID_SIZE)
coarse_result = bls.power(coarse_periods, duration=BLS_DURATIONS)

coarse_power = np.array(coarse_result.power)
coarse_periods_arr = np.array(coarse_result.period)
coarse_sde = (coarse_power - np.mean(coarse_power)) / np.std(coarse_power)

# Top 10 peaks
top_indices = np.argsort(coarse_sde)[::-1][:10]
print("Top 10 BLS peaks (coarse):")
for i, idx in enumerate(top_indices):
    print(f"  {i + 1}. P={coarse_periods_arr[idx]:.5f} d  "
          f"SDE={coarse_sde[idx]:.1f}  "
          f"dur={coarse_result.duration[idx]:.3f} d")

# ============================================================
# STEP 6: Filter out stellar rotation harmonics
# ============================================================
print("\n" + "=" * 60)
print("STEP 6: Filtering stellar harmonics from BLS peaks")
print("=" * 60)

planet_candidates = []
for idx in top_indices:
    p = coarse_periods_arr[idx]
    s = coarse_sde[idx]
    d = coarse_result.duration[idx]
    t0 = coarse_result.transit_time[idx]

    # Check if this period is a harmonic of stellar rotation
    is_harmonic = False
    for mult in [0.25, 0.333, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]:
        ref = mult * fitted_rotation_period
        if ref > 0.3 and abs(p - ref) / ref < HARMONIC_TOLERANCE:
            is_harmonic = True
            print(f"  P={p:.4f} d → harmonic ({mult}× P_rot={ref:.4f} d), skipping")
            break

    if not is_harmonic:
        planet_candidates.append((float(p), float(s), float(d), float(t0)))
        print(f"  P={p:.4f} d → CANDIDATE (SDE={s:.1f})")

if not planet_candidates:
    print("WARNING: No non-harmonic candidates found. Using strongest peak.")
    idx = top_indices[0]
    best_period = float(coarse_periods_arr[idx])
    best_duration = float(coarse_result.duration[idx])
    best_t0 = float(coarse_result.transit_time[idx])
else:
    best_period, best_sde, best_duration, best_t0 = planet_candidates[0]
    print(f"\nBest planet candidate: P={best_period:.5f} d, SDE={best_sde:.1f}")

# ============================================================
# STEP 7: Fine BLS refinement
# ============================================================
print("\n" + "=" * 60)
print("STEP 7: Fine BLS refinement")
print("=" * 60)

fine_lo = max(0.5, best_period - 0.35)
fine_hi = best_period + 0.35
fine_periods = np.linspace(fine_lo, fine_hi, FINE_GRID_SIZE)
fine_result = bls.power(fine_periods, duration=BLS_DURATIONS)
fine_power = np.array(fine_result.power)
fine_periods_arr = np.array(fine_result.period)
fine_best_idx = np.argmax(fine_power)
refined_period = float(fine_periods_arr[fine_best_idx])
refined_duration = float(fine_result.duration[fine_best_idx])
refined_t0 = float(fine_result.transit_time[fine_best_idx])
print(f"Refined period: {refined_period:.6f} d")
print(f"Refined duration: {refined_duration:.4f} d")

# ============================================================
# STEP 8: Ultra-fine BLS refinement
# ============================================================
print("\n" + "=" * 60)
print("STEP 8: Ultra-fine BLS refinement")
print("=" * 60)

ultra_lo = refined_period - 0.02
ultra_hi = refined_period + 0.02
ultra_periods = np.linspace(ultra_lo, ultra_hi, ULTRA_FINE_GRID_SIZE)
uf_result = bls.power(ultra_periods, duration=refined_duration)
uf_power = np.array(uf_result.power)
uf_periods = np.array(uf_result.period)
uf_best = np.argmax(uf_power)
final_period = float(uf_periods[uf_best])
final_t0 = float(uf_result.transit_time[uf_best])
print(f"Final period: {final_period:.6f} d")

# ============================================================
# STEP 9: Validate via phase-folding
# ============================================================
print("\n" + "=" * 60)
print("STEP 9: Phase-fold validation")
print("=" * 60)

phase = ((t_clean - final_t0) % final_period) / final_period
phase[phase > 0.5] -= 1.0

nbins = 200
bin_edges = np.linspace(-0.5, 0.5, nbins + 1)
bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
bin_flux = []
for i in range(nbins):
    m = (phase >= bin_edges[i]) & (phase < bin_edges[i + 1])
    if m.sum() > 0:
        bin_flux.append(np.median(f_clean[m]))
    else:
        bin_flux.append(np.nan)
bin_flux = np.array(bin_flux)

transit_depth = 1.0 - np.nanmin(bin_flux)
min_phase = bin_centers[np.nanargmin(bin_flux)]
oot_std = np.nanstd(bin_flux[np.abs(bin_centers) > 0.15])

print(f"Transit depth: {transit_depth * 100:.4f}%")
print(f"Phase of minimum: {min_phase:.3f}")
print(f"Out-of-transit scatter: {oot_std * 100:.4f}%")
print(f"Depth / OO