---
title: "Seismic Phase Association: From Waveforms to Earthquake Catalog"
category: earthquake-phase-association
tags:
  - seismology
  - phase-picking
  - event-association
  - seisbench
  - gamma
  - obspy
dependencies:
  - seisbench
  - obspy
  - torch
  - gamma-seismology
  - pandas
  - numpy
domain: geophysics
---

# Seismic Phase Association — Full Workflow Skill

This skill covers the end-to-end pipeline for **seismic phase association**: loading raw waveform data (MiniSEED) and station metadata, picking P and S wave arrivals with deep learning, associating those picks into discrete earthquake events, and writing a catalog CSV. The target metric is typically F1 score against a human-labeled catalog with a ±5 second matching window.

---

## 1. High-Level Workflow

1. **Load and inspect data** — Read the MiniSEED waveforms with ObsPy and the station CSV with Pandas. Understand the time window, number of stations, channel naming, and sampling rate.
2. **Prepare station metadata** — Build a lookup from `(network, station)` to `(longitude, latitude, elevation)`. Compute a local Cartesian coordinate system (km) centered on the station centroid for the associator.
3. **Pick P and S arrivals** — Use SeisBench's pretrained PhaseNet (or EQTransformer) to detect P and S phases on every station's 3-component waveform. Collect picks with timestamps, phase labels, and probabilities.
4. **Associate picks into events** — Feed picks + station coordinates into GaMMA (Gaussian Mixture Model Association). GaMMA clusters picks that are consistent with a common hypocenter under a velocity model.
5. **Post-process and deduplicate** — Remove duplicate events (within 3 s), keep the one with more picks. Verify the catalog covers the full time window.
6. **Write results** — Output a CSV with at least a `time` column in ISO 8601 format (no timezone suffix).

### Decision Criteria

| Decision | Recommendation | Why |
|---|---|---|
| Picking model | PhaseNet `instance` weights | Robust on regional data, fast inference, well-tested in SeisBench |
| Pick threshold | 0.2 (P and S) | Low threshold captures more true picks; the associator filters false positives |
| Associator | GaMMA | Handles overlapping events, works with 1D velocity model, available via pip |
| Velocity model | vp = 6.0 km/s, vs = vp/1.75 ≈ 3.43 km/s | Standard uniform crustal model for shallow seismicity |
| Dedup window | 3 seconds | Prevents double-counting without merging genuinely distinct events |

---

## 2. Environment Setup

```bash
# Core dependencies (seisbench pulls obspy and torch)
pip install seisbench

# GaMMA — the seismology phase associator (NOT the generic "gamma" package)
pip install gamma-seismology

# Verify
python3 -c "import seisbench; print('SeisBench', seisbench.__version__)"
python3 -c "import gamma; print('GaMMA OK')"
python3 -c "import obspy; print('ObsPy', obspy.__version__)"
```

**Critical**: The PyPI package is `gamma-seismology`, not `gamma`. Installing the wrong package gives an unrelated library.

---

## 3. Step-by-Step with Code

### 3.1 Load Waveform and Station Data

```python
import obspy
import pandas as pd
import numpy as np

# Load waveforms
stream = obspy.read("/root/data/wave.mseed")
print(f"Traces: {len(stream)}")
print(f"Time range: {stream[0].stats.starttime} — {stream[0].stats.endtime}")

# Load station metadata
stations_df = pd.read_csv("/root/data/stations.csv")
print(f"Station channels: {len(stations_df)}")
print(stations_df.head())

# Build unique station table (one row per station, not per channel)
station_info = (
    stations_df.groupby(["network", "station"])
    .agg({"longitude": "first", "latitude": "first", "elevation_m": "first"})
    .reset_index()
)
station_info["id"] = station_info["network"] + "." + station_info["station"]
print(f"Unique stations: {len(station_info)}")
```

### 3.2 Coordinate Projection (Lat/Lon → km)

GaMMA works in a local Cartesian frame. Project station coordinates to km relative to the network centroid.

```python
# Centroid
center_lat = station_info["latitude"].mean()
center_lon = station_info["longitude"].mean()

# Approximate conversion factors at the centroid latitude
km_per_deg_lat = 111.132
km_per_deg_lon = 111.132 * np.cos(np.radians(center_lat))

station_info["x(km)"] = (station_info["longitude"] - center_lon) * km_per_deg_lon
station_info["y(km)"] = (station_info["latitude"]  - center_lat) * km_per_deg_lat
station_info["z(km)"] = -station_info["elevation_m"] / 1000.0  # depth is positive down

print(f"X range: {station_info['x(km)'].min():.1f} to {station_info['x(km)'].max():.1f} km")
print(f"Y range: {station_info['y(km)'].min():.1f} to {station_info['y(km)'].max():.1f} km")
```

### 3.3 Phase Picking with SeisBench / PhaseNet

```python
import seisbench.models as sbm

# Load pretrained PhaseNet
model = sbm.PhaseNet.from_pretrained("instance")

# Group traces by station for 3-component input
station_streams = {}
for tr in stream:
    key = f"{tr.stats.network}.{tr.stats.station}"
    if key not in station_streams:
        station_streams[key] = obspy.Stream()
    station_streams[key] += tr

# Pick phases
all_picks = []
for sta_id, sta_stream in station_streams.items():
    try:
        # SeisBench classify returns a ClassifyOutput with .picks attribute
        output = model.classify(
            sta_stream,
            P_threshold=0.2,
            S_threshold=0.2,
            batch_size=256,
        )
        picks = output.picks  # list of Pick objects
        for pick in picks:
            all_picks.append({
                "id":         sta_id,
                "timestamp":  pick.peak_time.datetime,
                "prob":       pick.peak_value,
                "phase_type": pick.phase.upper(),  # "P" or "S"
            })
    except Exception as e:
        print(f"Warning: failed on {sta_id}: {e}")

picks_df = pd.DataFrame(all_picks)
picks_df["timestamp"] = pd.to_datetime(picks_df["timestamp"])
print(f"Total picks: {len(picks_df)} (P: {(picks_df['phase_type']=='P').sum()}, "
      f"S: {(picks_df['phase_type']=='S').sum()})")
```

**Key details**:
- `model.classify()` returns a `ClassifyOutput` object. Access picks via `.picks`, not by iterating the return value directly.
- Each `Pick` has `.peak_time` (UTCDateTime), `.peak_value` (probability), `.phase` (str).
- Use low thresholds (0.2) — the associator is the quality gate.

### 3.4 Phase Association with GaMMA

GaMMA requires specific config keys and a station DataFrame with `id`, `x(km)`, `y(km)`, `z(km)` columns.

```python
from gamma.utils import association

# Determine spatial bounds from station coordinates (with padding)
x_min, x_max = station_info["x(km)"].min() - 10, station_info["x(km)"].max() + 10
y_min, y_max = station_info["y(km)"].min() - 10, station_info["y(km)"].max() + 10
z_min, z_max = 0, 30  # depth range in km

# Determine time bounds from picks
t_start = picks_df["timestamp"].min()
t_end   = picks_df["timestamp"].max()

# Prepare picks for GaMMA: needs specific column names
picks_gamma = picks_df.rename(columns={
    "id":         "id",
    "timestamp":  "timestamp",
    "prob":       "prob",
    "phase_type": "type",
})

# Prepare station table for GaMMA
stations_gamma = station_info[["id", "x(km)", "y(km)", "z(km)"]].copy()

# GaMMA configuration
config = {
    "center":      (center_lon, center_lat),
    "xlim_degree":  [
        center_lon + x_min / km_per_deg_lon,
        center_lon + x_max / km_per_deg_lon,
    ],
    "ylim_degree":  [
        center_lat + y_min / km_per_deg_lat,
        center_lat + y_max / km_per_deg_lat,
    ],
    "z(km)":        [z_min, z_max],
    "x(km)":        [x_min, x_max],
    "y(km)":        [y_min, y_max],
    "vel":          {"p": 6.0, "s": 6.0 / 1.75},
    "dims":         ["x(km)", "y(km)", "z(km)"],
    "use_dbscan":   True,
    "use_amplitude": False,
    "dbscan_eps":   25,          # km — clustering radius
    "dbscan_min_samples": 3,     # minimum picks per event
    "min_picks_per_eq":   3,
    "max_sigma11":  3.0,         # seconds — max travel-time residual
    "oversample_factor": 5,
    "method":       "BGMM",
    "bfgs_bounds": (
        (x_min - 1, x_max + 1),
        (y_min - 1, y_max + 1),
        (z_min,     z_max + 1),
        (None,      None),       # origin time (unbounded)
    ),
    "initial_points": [0, 0, 10],  # fallback initial hypocenter guess
}

# Run association
catalogs, assigned_picks = association(
    picks_gamma,
    stations_gamma,
    config,
    method=config["method"],
)

print(f"Events found: {len(catalogs)}")
if len(catalogs) > 0:
    print(catalogs[0])
```

**Critical GaMMA config notes**:
- `bfgs_bounds` must be a tuple of 4 tuples: x, y, z, time. Without it, the optimizer crashes.
- `x(km)`, `y(km)`, `z(km)` keys must be present as both config bounds and station DataFrame columns.
- `vel` must be a dict with `"p"` and `"s"` keys (floats, in km/s).
- `use_amplitude: False` avoids needing magnitude calibration.
- `dbscan_eps` of ~25 km works for regional networks; reduce for dense arrays.

### 3.5 Post-Processing and Deduplication

```python
if len(catalogs) > 0:
    events_df = pd.DataFrame(catalogs)
    events_df["time"] = pd.to_datetime(events_df["time"])
    events_df = events_df.sort_values("time").reset_index(drop=True)

    # Deduplicate: if two events are within 3 seconds, keep the one with more picks
    keep = [True] * len(events_df)
    for i in range(1, len(events_df)):
        dt = (events_df.loc[i, "time"] - events_df.loc[i - 1, "time"]).total_seconds()
        if abs(dt) < 3.0:
            # Keep whichever has more picks
            if events_df.loc[i, "num_picks"] >= events_df.loc[i - 1, "num_picks"]:
                keep[i - 1] = False
            else:
                keep[i] = False
    events_df = events_df[keep].reset_index(drop=True)
    print(f"Events after dedup: {len(events_df)}")
else:
    events_df = pd.DataFrame(columns=["time"])
    print("WARNING: No events found!")
```

### 3.6 Write Results

```python
# Format time as ISO 8601 WITHOUT timezone
events_df["time"] = events_df["time"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
events_df.to_csv("/root/results.csv", index=False)
print(f"Wrote {len(events_df)} events to /root/results.csv")
```

The evaluator only reads the `time` column. Other columns (magnitude, location, etc.) are ignored but harmless to include.

---

## 4. Common Pitfalls

### 4.1 Wrong `gamma` Package
**Symptom**: `ImportError` or `gamma` has no `utils.association`.
**Fix**: Install `gamma-seismology`, not `gamma`. They are completely different packages on PyPI.

### 4.2 SeisBench `classify` Return Type
**Symptom**: Iterating over `model.classify(...)` gives unexpected objects or errors.
**Fix**: `classify()` returns a `ClassifyOutput` object. Access picks via `.picks` attribute:
```python
output = model.classify(stream, P_threshold=0.2, S_threshold=0.2)
picks = output.picks  # list of Pick objects
```

### 4.3 Missing GaMMA Config Keys
**Symptom**: `KeyError: 'z(km)'` or `KeyError: 'bfgs_bounds'` deep inside GaMMA internals.
**Fix**: GaMMA's `association()` function internally calls `init_centers()` and `scipy.optimize` which require `x(km)`, `y(km)`, `z(km)` as list bounds AND `bfgs_bounds` as a 4-tuple. Always include all of them.

### 4.4 Pick Thresholds Too High
**Symptom**: Very few events detected (low recall).
**Fix**: Use 0.2 for both P and S thresholds. The associator filters false positives — it's better to over-pick than under-pick.

### 4.5 Coordinate Mismatch
**Symptom**: All events cluster at origin or association finds zero events.
**Fix**: Ensure station `x(km)`, `y(km)`, `z(km)` use the same projection and centroid as the config bounds. Elevation should be converted to depth (positive down, in km).

### 4.6 Time Format Issues
**Symptom**: Evaluator can't parse times or all times show as NaT.
**Fix**: Use ISO 8601 without timezone: `2019-07-04T19:00:10.099000`. Do NOT append `+00:00` or `Z`.

### 4.7 Duplicate Events Inflating False Positives
**Symptom**: High recall but low precision; many events within seconds of each other.
**Fix**: Deduplicate within a 3-second window, keeping the event with more associated picks.

### 4.8 Channel Naming Variations
**Symptom**: Some stations produce no picks.
**Fix**: Stations may use different channel prefixes (HH, EH, HN, BH). PhaseNet handles this via SeisBench's internal channel mapping, but verify that 3-component grouping works. Group by `(network, station)`, not by channel.

---

## 5. Verification Checklist

Before submitting results, verify:

```python
results = pd.read_csv("/root/results.csv")
times = pd.to_datetime(results["time"])

print(f"Total events: {len(results)}")
print(f"Time range: {times.min()} to {times.max()}")

# Check for near-duplicates
diffs = times.sort_values().diff().dt.total_seconds().dropna()
print(f"Min inter-event gap: {diffs.min():.1f}s")
print(f"Events within 5s of each other: {(diffs < 5).sum()}")

# Sanity: expect tens to low hundreds of events for a 1-hour aftershock sequence
assert len(results) >= 10, "Suspiciously few events"
assert len(results) <= 500, "Suspiciously many events"
assert (diffs < 3).sum() == 0, "Duplicates remain"
```

---

## 6. Reference Implementation

This is the complete, self-contained script. Copy, adapt file paths, and run.

```python
#!/usr/bin/env python3
"""
Seismic Phase Association Pipeline
===================================
Input:  /root/data/wave.mseed   — MiniSEED waveforms
        /root/data/stations.csv — Station metadata (network, station, channel, lon, lat, elev, response)
Output: /root/results.csv       — Earthquake catalog with ISO timestamps

Dependencies: seisbench, obspy, gamma-seismology, pandas, numpy, torch
Install:      pip install seisbench gamma-seismology
"""

import warnings
warnings.filterwarnings("ignore")

import obspy
import numpy as np
import pandas as pd
import seisbench.models as sbm
from gamma.utils import association

# ──────────────────────────────────────────────
# 1. CONFIGURATION
# ──────────────────────────────────────────────
WAVEFORM_PATH  = "/root/data/wave.mseed"
STATION_PATH   = "/root/data/stations.csv"
OUTPUT_PATH    = "/root/results.csv"

VP = 6.0              # P-wave velocity (km/s)
VS = VP / 1.75        # S-wave velocity (km/s)
P_THRESHOLD = 0.2     # PhaseNet P pick threshold
S_THRESHOLD = 0.2     # PhaseNet S pick threshold
DEDUP_WINDOW = 3.0    # seconds — deduplication window
DBSCAN_EPS = 25       # km — DBSCAN clustering radius
DBSCAN_MIN = 3        # minimum picks per cluster
MIN_PICKS = 3         # minimum picks per event
MAX_SIGMA = 3.0       # max travel-time residual (seconds)
DEPTH_RANGE = (0, 30) # km
SPATIAL_PAD = 10      # km — padding around station extent

# ──────────────────────────────────────────────
# 2. LOAD DATA
# ──────────────────────────────────────────────
print("Loading waveforms...")
stream = obspy.read(WAVEFORM_PATH)
print(f"  {len(stream)} traces, {stream[0].stats.starttime} — {stream[0].stats.endtime}")

print("Loading stations...")
stations_raw = pd.read_csv(STATION_PATH)

# One row per station (aggregate across channels)
station_info = (
    stations_raw.groupby(["network", "station"])
    .agg({"longitude": "first", "latitude": "first", "elevation_m": "first"})
    .reset_index()
)
station_info["id"] = station_info["network"] + "." + station_info["station"]
print(f"  {len(station_info)} unique stations")

# ──────────────────────────────────────────────
# 3. COORDINATE PROJECTION (lat/lon → km)
# ──────────────────────────────────────────────
center_lat = station_info["latitude"].mean()
center_lon = station_info["longitude"].mean()
km_per_deg_lat = 111.132
km_per_deg_lon = 111.132 * np.cos(np.radians(center_lat))

station_info["x(km)"] = (station_info["longitude"] - center_lon) * km_per_deg_lon
station_info["y(km)"] = (station_info["latitude"]  - center_lat) * km_per_deg_lat
station_info["z(km)"] = -station_info["elevation_m"] / 1000.0  # depth positive down

x_min = station_info["x(km)"].min() - SPATIAL_PAD
x_max = station_info["x(km)"].max() + SPATIAL_PAD
y_min = station_info["y(km)"].min() - SPATIAL_PAD
y_max = station_info["y(km)"].max() + SPATIAL_PAD
z_min, z_max = DEPTH_RANGE

print(f"  Center: ({center_lon:.3f}, {center_lat:.3f})")
print(f"  X: [{x_min:.1f}, {x_max:.1f}] km, Y: [{y_min:.1f}, {y_max:.1f}] km")

# ──────────────────────────────────────────────
# 4. PHASE PICKING (PhaseNet via SeisBench)
# ──────────────────────────────────────────────
print("Loading PhaseNet model...")
model = sbm.PhaseNet.from_pretrained("instance")

# Group traces by station
station_streams = {}
for tr in stream:
    key = f"{tr.stats.network}.{tr.stats.station}"
    if key not in station_streams:
        station_streams[key] = obspy.Stream()
    station_streams[key] += tr

print(f"Picking phases on {len(station_streams)} station streams...")
all_picks = []
for sta_id, sta_stream in station_streams.items():
    try:
        output = model.classify(
            sta_stream,
            P_threshold=P_THRESHOLD,
            S_threshold=S_THRESHOLD,
            batch_size=256,
        )
        for pick in output.picks:
            all_picks.append({
                "id":        sta_id,
                "timestamp": pick.peak_time.datetime,
                "prob":      pick.peak_value,
                "type":      pick.phase.upper(),
            })
    except Exception as e:
        print(f"  Warning: {sta_id} failed: {e}")

picks_df = pd.DataFrame(all_picks)
picks_df["timestamp"] = pd.to_datetime(picks_df["timestamp"])
n_p = (picks_df["type"] == "P").sum()
n_s = (picks_df["type"] == "S").sum()
print(f"  Total picks: {len(picks_df)} (P: {n_p}, S: {n_s})")

# ──────────────────────────────────────────────
# 5. PHASE ASSOCIATION (GaMMA)
# ──────────────────────────────────────────────
print("Running GaMMA association...")

stations_gamma = station_info[["id", "x(km)", "y(km)", "z(km)"]].copy()

config = {
    "center":             (center_lon, center_lat),
    "xlim_degree":        [
        center_lon + x_min / km_per_deg_lon,
        center_lon + x_max / km_per_deg_lon,
    ],
    "ylim_degree":        [
        center_lat + y_min / km_per_deg_lat,
        center_lat + y_max / km_per_deg_lat,
    ],
    "x(km)":              [x_min, x_max],
    "y(km)":              [y_min, y_max],
    "z(km)":              [z_min, z_max],
    "vel":                {"p": VP, "s": VS},
    "dims":               ["x(km)", "y(km)", "z(km)"],
    "use_dbscan":         True,
    "use_amplitude":      False,
    "dbscan_eps":         DBSCAN_EPS,
    "dbscan_min_samples": DBSCAN_MIN,
    "min_picks_per_eq":   MIN_PICKS,
    "max_sigma11":        MAX_SIGMA,
    "oversample_factor":  5,
    "method":             "BGMM",
    "bfgs_bounds":        (
        (x_min - 1, x_max + 1),
        (y_min - 1, y_max + 1),
        (z_min,     z_max + 1),
        (None,      None),        # origin time — unbounded
    ),
    "initial_points":     [0, 0, 10],
}

catalogs, assigned_picks = association(
    picks_df,
    stations_gamma,
    config,
    method=config["method"],
)

print(f"  Raw events: {len(catalogs)}")

# ──────────────────────────────────────────────
# 6. POST-PROCESSING & DEDUPLICATION
# ──────────────────────────────────────────────
if len(catalogs) > 0:
    events_df = pd.DataFrame(catalogs)
    events_df["time"] = pd.to_datetime(events_df["time"])
    events_df = events_df.sort_values("time").reset_index(drop=True)

    # Deduplicate: within DEDUP_WINDOW seconds, keep event with more picks
    keep = [True] * len(events_df)
    for i in range(1, len(events_df)):
        dt = abs((events_df.loc[i, "time"] - events_df.loc[i - 1, "time"]).total_seconds())
        if dt < DEDUP_WINDOW:
            prev_picks = events_df.loc[i - 1].get("num_picks", 0)
            curr_picks = events_df.loc[i].get("num_picks", 0)
            if curr_picks >= prev_picks:
                keep[i - 1] = False
            else:
                keep[i] = False
    events_df = events_df[keep].reset_index(drop=True)
    print(f"  After dedup: {len(events_df)} events")
else:
    events_df = pd.DataFrame(columns=["time"])
    print("  WARNING: No events found — check picks and config")

# ──────────────────────────────────────────────
# 7. WRITE RESULTS
# ──────────────────────────────────────────────
events_df["time"] = pd.to_datetime(events_df["time"]).dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
events_df.to_csv(OUTPUT_PATH, index=False)
print(f"Wrote {len(events_df)} events to {OUTPUT_PATH}")

# ──────────────────────────────────────────────
# 8. SELF-VERIFICATION
# ──────────────────────────────────────────────
verify = pd.read_csv(OUTPUT_PATH)
times = pd.to_datetime(verify["time"]).sort_values().reset_index(drop=True)
gaps = times.diff().dt.total_seconds().dropna()

print("\n=== Verification ===")
print(f"  Events:    {len(verify)}")
print(f"  Time span: {times.min()} — {times.max()}")
print(f"  Min gap:   {gaps.min():.1f}s")
print(f"  Dupes <3s: {(gaps < 3).sum()}")

if len(verify) < 5:
    print("  ⚠ Very few events — consider lowering pick thresholds or DBSCAN min_samples")
if (gaps < 3).sum() > 0:
    print("  ⚠ Near-duplicates remain — increase DEDUP_WINDOW")

print("\nDone.")
```

---

## 7. Tuning Guide

If the F1 score is below the threshold, adjust these parameters in order:

| Problem | Symptom | Fix |
|---|---|---|
| Low recall | Too few events | Lower `P_THRESHOLD` / `S_THRESHOLD` to 0.1; lower `DBSCAN_MIN` to 2 |
| Low precision | Too many false events | Raise `MIN_PICKS` to 5; lower `MAX_SIGMA` to 2.0 |
| Missed distant events | Events only near center | Increase `SPATIAL_PAD` to 20+ km; increase `DBSCAN_EPS` to 40 |
| Merged events | Two real events counted as one | Decrease `DBSCAN_EPS` to 15; decrease `DEDUP_WINDOW` to 2.0 |
| Split events | One real event counted as two | Increase `DEDUP_WINDOW` to 5.0 |

### Alternative Picking Models

If PhaseNet `instance` underperforms on your data:

```python
# EQTransformer — sometimes better on noisy data
model = sbm.EQTransformer.from_pretrained("instance")

# PhaseNet with original weights — trained on different data distribution
model = sbm.PhaseNet.from_pretrained("original")
```

The rest of the pipeline stays identical — SeisBench's `classify()` API is consistent across models.

---

## 8. Library Quick Reference

### ObsPy
- `obspy.read(path)` → `Stream` (list of `Trace` objects)
- `trace.stats` → `network`, `station`, `channel`, `starttime`, `endtime`, `sampling_rate`
- `stream.select(station="STA")` → filtered `Stream`

### SeisBench
- `sbm.PhaseNet.from_pretrained("instance")` → pretrained model
- `model.classify(stream, P_threshold=0.2, S_threshold=0.2)` → `ClassifyOutput`
- `output.picks` → list of `Pick(peak_time, peak_value, phase)`

### GaMMA
- `from gamma.utils import association`
- `association(picks_df, stations_df, config, method="BGMM")` → `(catalogs, assigned_picks)`
- `catalogs` is a list of dicts with keys: `time`, `x(km)`, `y(km)`, `z(km)`, `num_picks`, etc.
- Station DataFrame must have columns: `id`, `x(km)`, `y(km)`, `z(km)`
- Picks DataFrame must have columns: `id`, `timestamp`, `prob`, `type`