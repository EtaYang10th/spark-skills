---
title: Gravitational Wave Detection via Matched Filtering with PyCBC
category: gravitational-wave-detection
tags:
  - pycbc
  - matched-filtering
  - signal-processing
  - gravitational-waves
  - grid-search
  - waveform-templates
  - SNR
domain: astrophysics / gravitational-wave data analysis
tools:
  - pycbc (2.8.x)
  - pandas
  - python3
applicability: >
  Tasks that require detecting gravitational wave signals in noisy detector
  data using matched filtering with template banks generated from waveform
  approximants. Covers data conditioning, PSD estimation, waveform generation,
  SNR time-series computation, and grid search over binary mass parameters.
---

# Gravitational Wave Detection via Matched Filtering with PyCBC

## 1. High-Level Workflow

Matched filtering is the optimal linear filter for detecting a known signal shape buried in stationary Gaussian noise. The procedure for gravitational-wave (GW) detection follows a well-defined pipeline:

1. **Load raw detector data** from a GWF frame file using PyCBC's I/O utilities.
2. **Condition the data** — high-pass filter to remove low-frequency seismic noise, crop transient filter edges, and zero-pad to a power-of-2 length for efficient FFTs.
3. **Estimate the Power Spectral Density (PSD)** of the conditioned strain using Welch's method, then apply inverse-spectrum truncation to suppress spectral leakage.
4. **Generate template waveforms** for each point in a grid of component masses and for each waveform approximant family.
5. **Perform matched filtering** — compute the SNR time series for each template against the conditioned data.
6. **Extract the peak SNR** for each template; track the best (highest SNR) template per approximant.
7. **Write results** to a CSV with one row per approximant.

### Decision Criteria

| Decision | Guidance |
|---|---|
| High-pass cutoff | 15 Hz is standard for LIGO-like detectors; removes seismic wall below ~20 Hz while preserving merger signals. |
| Crop duration | 2 seconds from each edge removes filter transients. |
| Zero-pad target | Next power of 2 ≥ data length. PyCBC's FFT is fastest at power-of-2 lengths. |
| PSD segment length | 4 seconds (4 × `delta_t` samples) is a good default for ~128 s of data at 4096 Hz. |
| Mass grid | Integer solar masses from 10 to 40 for each component (`m1 ≥ m2` to avoid duplicates). |
| Approximants | Use whatever the task specifies. Common families: `SEOBNRv4_opt` (EOB), `IMRPhenomD` (phenomenological), `TaylorT4` (post-Newtonian). |
| `low_frequency_cutoff` for filtering | 20 Hz — standard for Advanced LIGO sensitivity. |

---

## 2. Step-by-Step with Code

### 2.1 Load Raw Data

```python
from pycbc.frame import read_frame

# Parameters — adapt file path and channel to your task
gwf_path = "/root/data/PyCBC_T2_2.gwf"
channel = "H1:TEST-STRAIN"

strain = read_frame(gwf_path, channel)
print(f"Duration: {strain.duration}s  Sample rate: {1/strain.delta_t} Hz  Samples: {len(strain)}")
```

**Key points:**
- `read_frame` returns a `pycbc.types.TimeSeries`.
- Always verify duration and sample rate before proceeding — they determine PSD segment sizes and frequency resolution.

### 2.2 Condition the Data

```python
from pycbc.filter import highpass

# High-pass at 15 Hz to remove seismic noise
strain_hp = highpass(strain, 15.0)

# Crop 2 seconds from each edge to remove filter transients
crop_seconds = 2
crop_samples = int(crop_seconds / strain_hp.delta_t)
strain_cropped = strain_hp[crop_samples : len(strain_hp) - crop_samples]

# Zero-pad to next power of 2 for FFT efficiency
import math
target_len = 2 ** int(math.ceil(math.log2(len(strain_cropped))))

from pycbc.types import TimeSeries
import numpy as np

padded = np.zeros(target_len)
padded[:len(strain_cropped)] = strain_cropped.numpy()
conditioned = TimeSeries(padded, delta_t=strain_cropped.delta_t, epoch=strain_cropped.start_time)

print(f"Conditioned length: {len(conditioned)} samples ({conditioned.duration:.1f}s)")
```

**Why zero-pad to power of 2?**
PyCBC internally uses FFTs. Power-of-2 lengths are significantly faster and avoid edge effects when the template is shorter than the data.

### 2.3 Compute PSD

```python
from pycbc.psd import interpolate, inverse_spectrum_truncation

seg_len = int(4.0 / conditioned.delta_t)  # 4-second segments
seg_stride = seg_len // 2                  # 50% overlap

from pycbc.psd import welch
psd = welch(conditioned, seg_len=seg_len, seg_stride=seg_stride)

# Interpolate to match the frequency resolution of the FFT of conditioned data
from pycbc.types import FrequencySeries
delta_f = 1.0 / conditioned.duration
psd_interp = interpolate(psd, delta_f)

# Truncate inverse spectrum to suppress spectral leakage artifacts
psd_final = inverse_spectrum_truncation(
    psd_interp,
    seg_len,
    low_frequency_cutoff=15.0,
    trunc_method="hann"
)
```

**Critical:** The PSD must have the same `delta_f` as the FFT of the conditioned strain. Use `interpolate()` to ensure this.

### 2.4 Compute Strain FFT

```python
from pycbc.types import FrequencySeries
from pycbc.fft import fft

stilde = FrequencySeries(
    np.zeros(len(conditioned) // 2 + 1, dtype=np.complex128),
    delta_f=delta_f
)
fft(conditioned, stilde)
```

### 2.5 Generate a Template Waveform

```python
from pycbc.waveform import get_fd_waveform

def make_template(m1, m2, approximant, delta_f, f_lower=20.0):
    """Generate a frequency-domain template and resize to match data."""
    hp, _ = get_fd_waveform(
        approximant=approximant,
        mass1=m1,
        mass2=m2,
        delta_f=delta_f,
        f_lower=f_lower
    )
    return hp
```

**Template length mismatch — the #1 pitfall:**
`get_fd_waveform` returns a waveform whose length depends on the masses and approximant. It can be *shorter or longer* than `stilde`. You **must** resize the template to match `len(stilde)`:

```python
hp = make_template(m1, m2, approximant, delta_f)
hp.resize(len(stilde))  # Pads with zeros if shorter, RAISES ERROR if longer
```

If the template is **longer** than the data, `resize()` will fail. The solution is to ensure the conditioned data is zero-padded to a large enough power-of-2 length. For masses up to 40 M☉ at `delta_f ~ 1/128`, a length of 2^19 = 524288 (at 4096 Hz ≈ 128s) is sufficient. If you encounter this error, increase the zero-padding target.

### 2.6 Matched Filtering

```python
from pycbc.filter import matched_filter

snr_ts = matched_filter(hp, stilde, psd=psd_final, low_frequency_cutoff=20.0)

# Crop 4+4 seconds from edges to avoid filter artifacts in SNR time series
snr_crop = snr_ts[int(4.0 / conditioned.delta_t) : len(snr_ts) - int(4.0 / conditioned.delta_t)]
peak_snr = abs(snr_crop).max()
```

### 2.7 Grid Search

```python
import itertools

approximants = ["SEOBNRv4_opt", "IMRPhenomD", "TaylorT4"]
mass_range = range(10, 41)  # 10 to 40 inclusive

results = []

for approx in approximants:
    best_snr = 0
    best_m1, best_m2 = 0, 0

    for m1, m2 in itertools.product(mass_range, repeat=2):
        if m1 < m2:
            continue  # Convention: m1 >= m2, avoids duplicate pairs

        try:
            hp, _ = get_fd_waveform(
                approximant=approx,
                mass1=float(m1),
                mass2=float(m2),
                delta_f=delta_f,
                f_lower=20.0
            )
            hp.resize(len(stilde))
        except Exception as e:
            print(f"  SKIP {approx} m1={m1} m2={m2}: {e}")
            continue

        snr_ts = matched_filter(hp, stilde, psd=psd_final, low_frequency_cutoff=20.0)
        snr_crop = snr_ts[int(4.0 / conditioned.delta_t) : len(snr_ts) - int(4.0 / conditioned.delta_t)]
        peak = abs(snr_crop).max()

        if peak > best_snr:
            best_snr = peak
            best_m1, best_m2 = m1, m2

    total_mass = best_m1 + best_m2
    results.append({
        "approximant": approx,
        "snr": round(float(best_snr), 2),
        "total_mass": int(total_mass)
    })
    print(f"{approx}: SNR={best_snr:.2f}  m1={best_m1} m2={best_m2}  M_total={total_mass}")
```

**Performance note:** The grid has `31 × 31 = 961` pairs per approximant, but with `m1 >= m2` it's `31 + C(31,2) = 496` pairs. Three approximants → 1488 total evaluations. This takes 10–30 minutes depending on hardware.

### 2.8 Write CSV Output

```python
import pandas as pd

df = pd.DataFrame(results)
df.to_csv("/root/detection_results.csv", index=False)
print(df)
```

**Output format must be exactly:**
```csv
approximant,snr,total_mass
SEOBNRv4_opt,18.12,44
IMRPhenomD,18.28,44
TaylorT4,17.48,44
```

- 3 rows (one per approximant), 3 columns
- `snr` is a float, `total_mass` is an integer
- No extra whitespace, no index column

---

## 3. Common Pitfalls

### Pitfall 1: Template longer than data → `resize()` fails
**Symptom:** `ValueError: Length of template and data must match` or resize error.
**Cause:** `get_fd_waveform` produces a waveform with more frequency bins than `stilde` when the data segment is too short or not zero-padded.
**Fix:** Always zero-pad the conditioned strain to a power-of-2 length that is large enough. For masses up to 40 M☉ at 4096 Hz sample rate, 2^19 = 524288 samples works. Compute `stilde` and PSD from this padded data, then `hp.resize(len(stilde))` will always succeed (templates are shorter).

### Pitfall 2: PSD `delta_f` mismatch
**Symptom:** Cryptic shape mismatch errors in `matched_filter`.
**Cause:** The PSD was computed with a different `delta_f` than the data's FFT.
**Fix:** Always use `interpolate(psd, delta_f)` where `delta_f = 1.0 / conditioned.duration`.

### Pitfall 3: Not cropping SNR edges
**Symptom:** Spuriously high SNR values at the boundaries of the time series.
**Cause:** Filter transients at the start and end of the SNR time series.
**Fix:** Crop at least 4 seconds from each end of the SNR time series before taking the maximum.

### Pitfall 4: Forgetting `m1 >= m2` convention
**Symptom:** Double-counting mass pairs, wasting compute time, or getting inconsistent results.
**Fix:** Skip pairs where `m1 < m2`. This halves the search space and follows GW convention.

### Pitfall 5: Not converting masses to float
**Symptom:** Some approximants may behave unexpectedly with integer mass inputs.
**Fix:** Pass `mass1=float(m1), mass2=float(m2)` to `get_fd_waveform`.

### Pitfall 6: Forgetting to high-pass before PSD estimation
**Symptom:** PSD dominated by low-frequency noise, matched filter SNR is suppressed.
**Fix:** Always high-pass the raw strain (15 Hz) and crop edges before computing the PSD.

### Pitfall 7: Writing CSV with wrong column names or extra index
**Symptom:** Validator rejects the output file.
**Fix:** Use exact column names `approximant,snr,total_mass`. Pass `index=False` to `to_csv()`.

---

## 4. Validation Checklist

Before finalizing, verify:

```python
import pandas as pd

df = pd.read_csv("/root/detection_results.csv")

assert list(df.columns) == ["approximant", "snr", "total_mass"], "Wrong columns"
assert len(df) == 3, "Must have exactly 3 rows"
assert set(df["approximant"]) == {"SEOBNRv4_opt", "IMRPhenomD", "TaylorT4"}, "Missing approximant"
assert pd.api.types.is_numeric_dtype(df["snr"]), "snr must be numeric"
assert pd.api.types.is_numeric_dtype(df["total_mass"]), "total_mass must be numeric"
assert (df["snr"] > 0).all(), "SNR must be positive"
assert (df["total_mass"] >= 20).all() and (df["total_mass"] <= 80).all(), "total_mass out of range"
print("All checks passed")
```

Expected constraints from the grid:
- `total_mass` is between 20 (10+10) and 80 (40+40)
- `snr` should be a meaningful positive number (typically > 5 for a real detection)
- All three approximants must be present

---

## 5. Reference Implementation

This is a complete, self-contained script. Copy, adapt the file path / channel / mass range as needed, and run.

```python
#!/usr/bin/env python3
"""
Gravitational Wave Detection via Matched Filtering — Full Pipeline
==================================================================
Reads raw GWF frame data, conditions it, estimates PSD, performs a grid
search over (m1, m2, approximant) using matched filtering, and writes
the best SNR per approximant to a CSV file.

Requirements: pycbc, pandas, numpy
"""

import math
import itertools
import numpy as np
import pandas as pd

from pycbc.frame import read_frame
from pycbc.filter import highpass, matched_filter
from pycbc.psd import welch, interpolate, inverse_spectrum_truncation
from pycbc.waveform import get_fd_waveform
from pycbc.types import TimeSeries, FrequencySeries
from pycbc.fft import fft

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
GWF_PATH = "/root/data/PyCBC_T2_2.gwf"
CHANNEL = "H1:TEST-STRAIN"
OUTPUT_CSV = "/root/detection_results.csv"

HIGHPASS_FREQ = 15.0          # Hz — removes seismic noise
CROP_SECONDS = 2              # seconds to crop from each edge after high-pass
F_LOWER = 20.0                # Hz — low-frequency cutoff for waveforms & filtering
PSD_SEG_DURATION = 4.0        # seconds per Welch segment

APPROXIMANTS = ["SEOBNRv4_opt", "IMRPhenomD", "TaylorT4"]
MASS_LO = 10                  # solar masses (inclusive)
MASS_HI = 40                  # solar masses (inclusive)

SNR_CROP_SECONDS = 4          # seconds to crop from SNR time series edges

# ─── STEP 1: LOAD RAW DATA ──────────────────────────────────────────────────
print("[1/6] Loading data...")
strain = read_frame(GWF_PATH, CHANNEL)
sample_rate = 1.0 / strain.delta_t
print(f"  Duration: {strain.duration:.1f}s  Sample rate: {sample_rate:.0f} Hz  "
      f"Samples: {len(strain)}")

# ─── STEP 2: CONDITION DATA ─────────────────────────────────────────────────
print("[2/6] Conditioning data...")

# High-pass filter
strain_hp = highpass(strain, HIGHPASS_FREQ)

# Crop filter transients
crop_samples = int(CROP_SECONDS / strain_hp.delta_t)
strain_cropped = strain_hp[crop_samples : len(strain_hp) - crop_samples]

# Zero-pad to next power of 2
target_len = 2 ** int(math.ceil(math.log2(len(strain_cropped))))
padded = np.zeros(target_len)
padded[:len(strain_cropped)] = strain_cropped.numpy()
conditioned = TimeSeries(padded, delta_t=strain_cropped.delta_t,
                         epoch=strain_cropped.start_time)
print(f"  Conditioned: {len(conditioned)} samples ({conditioned.duration:.1f}s)")

# ─── STEP 3: ESTIMATE PSD ───────────────────────────────────────────────────
print("[3/6] Estimating PSD...")
seg_len = int(PSD_SEG_DURATION / conditioned.delta_t)
seg_stride = seg_len // 2
delta_f = 1.0 / conditioned.duration

psd_raw = welch(conditioned, seg_len=seg_len, seg_stride=seg_stride)
psd_interp = interpolate(psd_raw, delta_f)
psd_final = inverse_spectrum_truncation(
    psd_interp, seg_len,
    low_frequency_cutoff=HIGHPASS_FREQ,
    trunc_method="hann"
)
print(f"  PSD delta_f: {psd_final.delta_f:.6f} Hz  Length: {len(psd_final)}")

# ─── STEP 4: FFT OF CONDITIONED STRAIN ──────────────────────────────────────
print("[4/6] Computing strain FFT...")
n_freq = len(conditioned) // 2 + 1
stilde = FrequencySeries(np.zeros(n_freq, dtype=np.complex128), delta_f=delta_f)
fft(conditioned, stilde)
print(f"  stilde length: {len(stilde)}")

# ─── STEP 5: GRID SEARCH ────────────────────────────────────────────────────
print("[5/6] Running matched filtering grid search...")
mass_range = range(MASS_LO, MASS_HI + 1)
snr_crop_samples = int(SNR_CROP_SECONDS / conditioned.delta_t)

results = []

for approx in APPROXIMANTS:
    best_snr = 0.0
    best_m1, best_m2 = 0, 0
    n_evaluated = 0
    n_skipped = 0

    for m1, m2 in itertools.product(mass_range, repeat=2):
        if m1 < m2:
            continue  # enforce m1 >= m2 convention

        try:
            hp, _ = get_fd_waveform(
                approximant=approx,
                mass1=float(m1),
                mass2=float(m2),
                delta_f=delta_f,
                f_lower=F_LOWER
            )
            hp.resize(len(stilde))
        except Exception as e:
            n_skipped += 1
            continue

        snr_ts = matched_filter(hp, stilde, psd=psd_final,
                                low_frequency_cutoff=F_LOWER)
        snr_crop = snr_ts[snr_crop_samples : len(snr_ts) - snr_crop_samples]
        peak = float(abs(snr_crop).max())
        n_evaluated += 1

        if peak > best_snr:
            best_snr = peak
            best_m1, best_m2 = m1, m2

    total_mass = best_m1 + best_m2
    results.append({
        "approximant": approx,
        "snr": round(best_snr, 2),
        "total_mass": int(total_mass)
    })
    print(f"  {approx}: SNR={best_snr:.2f}  m1={best_m1} m2={best_m2}  "
          f"M_total={total_mass}  evaluated={n_evaluated} skipped={n_skipped}")

# ─── STEP 6: WRITE OUTPUT ───────────────────────────────────────────────────
print("[6/6] Writing results...")
df = pd.DataFrame(results)
df.to_csv(OUTPUT_CSV, index=False)
print(f"\nResults written to {OUTPUT_CSV}")
print(df.to_string(index=False))

# ─── SELF-VALIDATION ────────────────────────────────────────────────────────
print("\n--- Validation ---")
df_check = pd.read_csv(OUTPUT_CSV)
assert list(df_check.columns) == ["approximant", "snr", "total_mass"]
assert len(df_check) == 3
assert set(df_check["approximant"]) == set(APPROXIMANTS)
assert pd.api.types.is_numeric_dtype(df_check["snr"])
assert pd.api.types.is_numeric_dtype(df_check["total_mass"])
assert (df_check["snr"] > 0).all()
assert (df_check["total_mass"] >= 2 * MASS_LO).all()
assert (df_check["total_mass"] <= 2 * MASS_HI).all()
print("All validation checks passed!")
```

---

## 6. PyCBC API Quick Reference

| Function | Purpose | Key Parameters |
|---|---|---|
| `pycbc.frame.read_frame(path, channel)` | Load GWF frame file | Returns `TimeSeries` |
| `pycbc.filter.highpass(ts, freq)` | Butterworth high-pass filter | `freq` in Hz |
| `pycbc.psd.welch(ts, seg_len, seg_stride)` | Welch PSD estimate | Segment length in samples |
| `pycbc.psd.interpolate(psd, delta_f)` | Resample PSD to target `delta_f` | Must match data FFT |
| `pycbc.psd.inverse_spectrum_truncation(psd, seg_len, ...)` | Suppress spectral leakage | `trunc_method="hann"` |
| `pycbc.waveform.get_fd_waveform(...)` | Generate frequency-domain template | Returns `(hp, hc)` |
| `pycbc.filter.matched_filter(template, data, psd, ...)` | Compute SNR time series | `low_frequency_cutoff` in Hz |
| `TimeSeries.resize(n)` / `FrequencySeries.resize(n)` | Zero-pad or truncate | Raises if truncating non-zero data |

---

## 7. Environment Notes

- **Runtime:** Python 3.9+ with `pycbc >= 2.8`, `pandas`, `numpy` pre-installed.
- **Data format:** GWF (Gravitational Wave Frame) files, read via `pycbc.frame.read_frame`.
- **Compute time:** ~10–30 minutes for the full 1488-template grid search on a single CPU core. No GPU acceleration needed for this scale.
- **Memory:** Peak usage ~2 GB for 524288-sample FFTs with complex128 arrays. Comfortable on most systems.