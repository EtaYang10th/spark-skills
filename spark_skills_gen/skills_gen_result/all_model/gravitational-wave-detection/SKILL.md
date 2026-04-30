---
id: gravitational-wave-detection
title: Gravitational Wave Detection via Matched Filtering (PyCBC)
version: 1.0.0
tags: [gravitational-waves, pycbc, matched-filtering, signal-processing, astronomy]
---

## Overview

This skill covers detecting gravitational wave signals from binary black hole (BBH) mergers using PyCBC's matched filtering pipeline. The core workflow: load GWF data → condition (resample + bandpass + PSD) → generate template bank → matched filter → extract peak SNR per approximant → write CSV.

---

## Module 1: Data Inspection and Environment Check

Before writing any analysis code, verify the data and environment.

```python
import pycbc
from pycbc.frame import read_frame

print(pycbc.__version__)  # confirm >= 2.x

# Inspect the data file
strain = read_frame('path/to/data.gwf', 'DETECTOR:CHANNEL-NAME')
print(f"Duration: {strain.duration}s, Sample rate: {strain.sample_rate}Hz")
print(f"Start GPS: {strain.start_time}, End GPS: {strain.end_time}")
```

Key things to confirm:
- Channel name format: `H1:CHANNEL-NAME` or `L1:CHANNEL-NAME`
- Duration (affects PSD estimation — need at least ~8s segments)
- Sample rate (resample to 2048 Hz for BBH searches to save compute time)

---

## Module 2: Optimized Matched Filter Pipeline

The most common failure mode is **timeout** from an oversized template bank or redundant computation. Structure the script for speed.

```python
import numpy as np
import pandas as pd
from pycbc.frame import read_frame
from pycbc.filter import matched_filter, resample_to_delta_t
from pycbc.psd import interpolate, inverse_spectrum_truncation
from pycbc.waveform import get_fd_waveform
from pycbc.types import FrequencySeries
import pycbc.psd

# --- 1. Load and condition data ---
strain = read_frame('data.gwf', 'H1:CHANNEL-NAME')

# Resample to 2048 Hz — sufficient for BBH, halves compute cost vs 4096 Hz
strain = resample_to_delta_t(strain, 1.0 / 2048)

# Crop edges to remove filter artifacts (4s each side is typical)
conditioned = strain.crop(4, 4)

# --- 2. Estimate PSD once — reuse for all templates ---
psd = pycbc.psd.welch(conditioned,
                      seg_len=int(4 * conditioned.sample_rate),
                      seg_stride=int(2 * conditioned.sample_rate))
psd = interpolate(psd, conditioned.delta_f)
psd = inverse_spectrum_truncation(psd,
                                  int(4 * conditioned.sample_rate),
                                  low_frequency_cutoff=20.0)

# --- 3. Grid search — skip symmetric duplicates ---
f_lower = 20.0
approximants = ["SEOBNRv4_opt", "IMRPhenomD", "TaylorT4"]
mass_range = range(10, 41)  # integer solar masses

results = []

for approx in approximants:
    best_snr = 0.0
    best_mass = None

    for m1 in mass_range:
        for m2 in mass_range:
            if m2 > m1:
                continue  # avoid duplicate (m1,m2) / (m2,m1) pairs

            try:
                hp, _ = get_fd_waveform(
                    approximant=approx,
                    mass1=m1, mass2=m2,
                    delta_f=conditioned.delta_f,
                    f_lower=f_lower,
                    distance=100  # Mpc — arbitrary, SNR is normalized
                )
            except Exception:
                continue  # some mass combos may be outside model validity

            # Resize template to match data length
            hp.resize(len(conditioned) // 2 + 1)

            try:
                snr_ts = matched_filter(hp, conditioned,
                                        psd=psd,
                                        low_frequency_cutoff=f_lower)
                snr_ts = snr_ts.crop(4, 4)  # remove edge effects
                peak = float(abs(snr_ts).max())
            except Exception:
                continue

            if peak > best_snr:
                best_snr = peak
                best_mass = m1 + m2

    results.append({
        'approximant': approx,
        'snr': best_snr,
        'total_mass': best_mass
    })

# --- 4. Write output ---
df = pd.DataFrame(results, columns=['approximant', 'snr', 'total_mass'])
df.to_csv('/root/detection_results.csv', index=False)
print(df)
```

### Performance tips
- Resample to 2048 Hz before anything else — biggest single speedup
- Compute PSD once outside the loop
- Skip `m2 > m1` to halve the search space
- Wrap `get_fd_waveform` and `matched_filter` in try/except — some mass combos are outside a model's validity range and will raise without crashing the whole run
- Use `timeout` at the shell level as a safety net: `timeout 1400 python3 script.py`

---

## Module 3: Output Validation

Before finalizing, verify the CSV matches the expected contract:

```python
import pandas as pd

df = pd.read_csv('/root/detection_results.csv')

# Must have exactly 3 rows — one per approximant
assert len(df) == 3, f"Expected 3 rows, got {len(df)}"

# All three approximants must be present
expected = {"SEOBNRv4_opt", "IMRPhenomD", "TaylorT4"}
assert set(df['approximant']) == expected

# SNR should be physically meaningful (> 8 for a real detection, < ~100)
assert df['snr'].between(8, 100).all(), f"SNR out of range:\n{df}"

# Total mass must be an integer within the searched grid (20–80 for this range)
assert df['total_mass'].apply(lambda x: x == int(x)).all()
assert df['total_mass'].between(20, 80).all()

print("All checks passed")
print(df)
```

---

## Common Pitfalls

**Timeout from oversized template bank**
Running at 4096 Hz with no duplicate-skipping over a 31×31 grid (~961 templates × 3 approximants) easily exceeds 1800s. Always resample to 2048 Hz and skip `m2 > m1`.

**PSD recomputed inside the loop**
PSD estimation is expensive. Compute it once on the conditioned strain and reuse it for every template.

**Edge effects not cropped from SNR time series**
`matched_filter` output has corrupted edges. Always `.crop(4, 4)` (or similar) before taking the max SNR, or you'll get spurious peaks.

**Template length mismatch**
`hp` from `get_fd_waveform` may be shorter than the data's frequency series. Always call `hp.resize(len(conditioned) // 2 + 1)` before filtering.

**Silent failures from invalid mass combinations**
Some approximants (especially `SEOBNRv4_opt`) have validity bounds. A bare call without try/except will silently skip or crash. Wrap both waveform generation and filtering.

**Wrong column order or missing header**
The output CSV must have columns in exactly the order `approximant,snr,total_mass` with no index column. Use `df.to_csv(path, index=False)`.

**Not cropping strain edges before PSD estimation**
Raw GWF data often has glitches or filter transients at the boundaries. Crop 4s from each end of the strain before computing the PSD and before matched filtering.
