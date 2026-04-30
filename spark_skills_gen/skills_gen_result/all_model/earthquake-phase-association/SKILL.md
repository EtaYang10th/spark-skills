---
name: earthquake-phase-association-with-seisbench-and-grid-voting
description: Use SeisBench phase picking plus velocity-model-based grid association to convert miniSEED waveform data and per-channel station metadata into a de-duplicated earthquake event catalog with origin times.
category: geophysics
tags:
  - seismology
  - earthquake
  - phase-picking
  - association
  - seisbench
  - obspy
  - mseed
  - csv
tools:
  - python
  - seisbench
  - obspy
  - pandas
  - numpy
  - scipy
---

# Earthquake Phase Association with SeisBench and Grid Voting

This skill covers a robust pattern for **detecting earthquake event times** from:
- waveform data in **miniSEED**
- station metadata in **CSV**
- a simple **uniform velocity model** (`vp`, `vs`)

The goal is usually to produce a CSV catalog where each row is an event and the required field is:

```text
time
```

with time formatted as **ISO without timezone**, e.g.:

```text
2019-07-04T19:00:09.480
```

This workflow is especially effective when:
- station metadata gives **longitude/latitude/elevation**
- picks must be generated from waveforms using **SeisBench**
- a full relocation package is unavailable or unnecessary
- evaluation only cares about **event times**, not precise hypocenters

The successful pattern is:

1. Load and clean station/channel metadata.
2. Read waveforms and normalize channel/station identifiers.
3. Run a pretrained SeisBench picker such as **PhaseNet**.
4. Aggregate picks by station and phase.
5. Associate picks into events by **travel-time voting over a spatial grid**.
6. De-duplicate nearby solutions and export a sorted catalog.
7. Sanity-check timing, spacing, and duplicate counts before finalizing.

---

## When to Use This Approach

Choose this path early if:

- the task explicitly asks for **P/S picks from SeisBench**
- the deliverable is only **event timestamps**
- you have a **uniform velocity model**, not a detailed 3D earth model
- you need a method that is easy to debug and performs reasonably without elaborate inversion

Prefer a **grid-voting association** over more complicated machinery when:
- the station network spans a moderate area
- station metadata is available
- validation uses **time proximity** rather than exact location
- speed and robustness matter more than perfect hypocenter estimation

---

## Expected Inputs and Conventions

### Waveforms
- Format: **miniSEED**
- Read with `obspy.read`

### Stations CSV
Expected columns:
- `network`
- `station`
- `channel`
- `longitude`
- `latitude`
- `elevation_m`
- `response`

Important:
- each row represents **one channel**, not one station
- for association, collapse to **one row per station** after validating coordinates are consistent

### Velocity Model
Uniform model:
- `vp = 6.0 km/s`
- `vs = vp / 1.75`

### Output
Write a CSV to the expected location with at least:
- `time`

Optional extra columns:
- `stations`
- `p_picks`
- `s_picks`
- `score`

---

# High-Level Workflow

## 1) Inspect data and normalize identifiers

Why:
- Association fails silently if waveform trace IDs do not line up with station metadata.
- miniSEED often contains blank location codes or inconsistent channel naming.
- station CSV is per-channel, so you must reduce it to per-station coordinates.

What to verify:
- waveform stream is non-empty
- station CSV contains required columns
- network/station/channel identifiers are strings and stripped
- station coordinates are finite
- per-station coordinates are internally consistent enough to collapse

### Code: load and validate station metadata

```python
import pandas as pd
import numpy as np

REQUIRED_STATION_COLUMNS = {
    "network", "station", "channel", "longitude", "latitude", "elevation_m", "response"
}

def load_station_metadata(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = REQUIRED_STATION_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required station columns: {sorted(missing)}")

    for col in ["network", "station", "channel"]:
        df[col] = df[col].astype(str).str.strip()

    for col in ["longitude", "latitude", "elevation_m", "response"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["network", "station", "channel", "longitude", "latitude"])
    if df.empty:
        raise ValueError("No usable station rows after cleaning")

    # Collapse per-channel rows into one per station.
    grouped = []
    for (net, sta), g in df.groupby(["network", "station"], sort=False):
        lon = g["longitude"].median()
        lat = g["latitude"].median()
        elev = g["elevation_m"].median() if g["elevation_m"].notna().any() else 0.0
        grouped.append({
            "network": net,
            "station": sta,
            "longitude": float(lon),
            "latitude": float(lat),
            "elevation_m": float(elev) if np.isfinite(elev) else 0.0,
        })

    stations = pd.DataFrame(grouped).drop_duplicates(["network", "station"])
    if stations.empty:
        raise ValueError("No station-level metadata could be derived")

    return stations
```

### Code: inspect waveform stream

```python
from obspy import read

def load_stream(path: str):
    st = read(path)
    if len(st) == 0:
        raise ValueError("Waveform stream is empty")
    return st

def summarize_stream(st):
    rows = []
    for tr in st:
        rows.append({
            "id": tr.id,
            "network": str(tr.stats.network).strip(),
            "station": str(tr.stats.station).strip(),
            "location": str(getattr(tr.stats, "location", "")).strip(),
            "channel": str(tr.stats.channel).strip(),
            "starttime": str(tr.stats.starttime),
            "endtime": str(tr.stats.endtime),
            "npts": int(tr.stats.npts),
            "sampling_rate": float(tr.stats.sampling_rate),
        })
    return pd.DataFrame(rows)
```

---

## 2) Harmonize waveform traces with station rows

Why:
- SeisBench picks will be emitted on trace/station identifiers from the waveform stream.
- Association should usually happen at the **station level**, not per channel.
- Many datasets have multiple components per station; that is good for picking but must be merged for association.

Decision criteria:
- use `(network, station)` as the station key unless the same station name appears at multiple coordinates in the same network
- ignore channel-level differences during association

### Code: build station lookup keyed by waveform IDs

```python
def build_station_lookup(stations: pd.DataFrame) -> dict:
    lookup = {}
    for _, row in stations.iterrows():
        key = (str(row["network"]).strip(), str(row["station"]).strip())
        lookup[key] = {
            "longitude": float(row["longitude"]),
            "latitude": float(row["latitude"]),
            "elevation_m": float(row["elevation_m"]),
        }
    return lookup

def trace_station_key(tr):
    return (str(tr.stats.network).strip(), str(tr.stats.station).strip())
```

---

## 3) Run SeisBench picking on the waveform stream

Why:
- Modern pretrained models such as **PhaseNet** are strong defaults.
- They outperform simplistic STA/LTA for noisy multi-station event catalogs.
- The task specifically calls for deep learning pickers in SeisBench.

Recommended default:
- `seisbench.models.PhaseNet.from_pretrained(...)`
- run classification/picking on the full stream
- keep both **P** and **S** picks
- filter low-confidence picks

Practical note:
- model names can vary across SeisBench versions. If one pretrained weight name fails, try another available pretrained option for the same model family.

### Code: run PhaseNet picks robustly

```python
import warnings
import seisbench.models as sbm

def load_phasenet_model():
    # Try common pretrained names first, then fallback.
    candidates = [
        ("PhaseNet", "instance"),
        ("PhaseNet", "original"),
        ("PhaseNet", "ethz"),
        ("PhaseNet", None),
    ]
    errors = []
    for model_name, pretrained_name in candidates:
        try:
            ModelCls = getattr(sbm, model_name)
            if pretrained_name is None:
                model = ModelCls.from_pretrained()
            else:
                model = ModelCls.from_pretrained(pretrained_name)
            return model
        except Exception as e:
            errors.append(f"{model_name}/{pretrained_name}: {e}")
    raise RuntimeError("Could not load a PhaseNet pretrained model:\n" + "\n".join(errors))

def classify_picks(model, stream, batch_size=256, p_threshold=0.3, s_threshold=0.3):
    """
    Returns a list-like object of picks from SeisBench.
    Threshold keys depend on model wrapper behavior; this function guards best-effort usage.
    """
    kwargs_list = [
        {"batch_size": batch_size, "P_threshold": p_threshold, "S_threshold": s_threshold},
        {"batch_size": batch_size, "p_threshold": p_threshold, "s_threshold": s_threshold},
        {"batch_size": batch_size},
        {},
    ]

    last_err = None
    for kwargs in kwargs_list:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                picks = model.classify(stream, **kwargs)
            return picks
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Model classify failed for all tested argument patterns: {last_err}")
```

---

## 4) Convert SeisBench outputs into a clean pick table

Why:
- downstream association needs a simple tabular format:
  - station key
  - phase (`P` or `S`)
  - pick time
  - probability/score if available
- SeisBench object structures can differ slightly by version

What to enforce:
- phase labels normalized to uppercase `P` / `S`
- picks from stations missing metadata are discarded
- keep only the **best few** picks per station and phase in a local time neighborhood to reduce duplicates

### Code: normalize picks

```python
from obspy import UTCDateTime

def _safe_getattr(obj, names, default=None):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
    return default

def picks_to_dataframe(picks, station_lookup: dict) -> pd.DataFrame:
    rows = []

    iterable = picks.picks if hasattr(picks, "picks") else picks
    for pk in iterable:
        network = str(_safe_getattr(pk, ["network"], "")).strip()
        station = str(_safe_getattr(pk, ["station"], "")).strip()
        phase = str(_safe_getattr(pk, ["phase", "phase_hint", "type"], "")).upper().strip()

        if phase not in {"P", "S"}:
            continue

        t = _safe_getattr(pk, ["peak_time", "start_time", "time"])
        if t is None:
            continue

        try:
            t = UTCDateTime(t).datetime
        except Exception:
            continue

        score = _safe_getattr(pk, ["peak_value", "probability", "score"], np.nan)
        try:
            score = float(score)
        except Exception:
            score = np.nan

        key = (network, station)
        if key not in station_lookup:
            continue

        rows.append({
            "network": network,
            "station": station,
            "phase": phase,
            "time": pd.Timestamp(t),
            "score": score,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("No usable P/S picks matched station metadata")

    df = df.sort_values(["time", "network", "station", "phase"]).reset_index(drop=True)
    return df
```

### Code: suppress duplicate picks per station-phase

```python
def suppress_nearby_duplicate_picks(picks_df: pd.DataFrame, min_sep_seconds: float = 1.5) -> pd.DataFrame:
    if picks_df.empty:
        return picks_df.copy()

    kept = []
    for (net, sta, phase), g in picks_df.groupby(["network", "station", "phase"], sort=False):
        g = g.sort_values(["time", "score"], ascending=[True, False]).reset_index(drop=True)
        accepted = []
        for _, row in g.iterrows():
            t = row["time"]
            if not accepted:
                accepted.append(row)
                continue
            dt = min(abs((t - r["time"]).total_seconds()) for r in accepted)
            if dt >= min_sep_seconds:
                accepted.append(row)
            else:
                # replace weaker overlapping pick if current is stronger
                idx = np.argmin([abs((t - r["time"]).total_seconds()) for r in accepted])
                if pd.notna(row["score"]) and (pd.isna(accepted[idx]["score"]) or row["score"] > accepted[idx]["score"]):
                    accepted[idx] = row
        kept.extend(accepted)

    out = pd.DataFrame(kept).sort_values("time").reset_index(drop=True)
    return out
```

---

## 5) Build a spatial grid and travel-time calculator

Why:
- if event location is unknown, travel-time consistency can still be tested by scanning candidate source points
- under a uniform velocity model, origin time is:
  - `origin = pick_time - distance / velocity`
- if many stations produce similar origin times for the same grid point, that suggests a real event

Coordinate conventions:
- longitude/latitude in **degrees**
- horizontal distance in **km**
- elevation in **meters**, usually a small correction; safe to ignore initially for event-time-only tasks
- use **geodesic or haversine** distance, not raw degree subtraction

### Code: haversine distance and grid generation

```python
import numpy as np

EARTH_RADIUS_KM = 6371.0

def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    return EARTH_RADIUS_KM * c

def build_grid_from_stations(stations: pd.DataFrame, padding_deg: float = 0.15, n_lon: int = 25, n_lat: int = 25):
    lon_min = stations["longitude"].min() - padding_deg
    lon_max = stations["longitude"].max() + padding_deg
    lat_min = stations["latitude"].min() - padding_deg
    lat_max = stations["latitude"].max() + padding_deg

    lons = np.linspace(lon_min, lon_max, n_lon)
    lats = np.linspace(lat_min, lat_max, n_lat)

    grid = np.array([(lon, lat) for lat in lats for lon in lons], dtype=float)
    if grid.size == 0:
        raise ValueError("Generated empty source grid")
    return grid
```

### Code: precompute station travel times

```python
def precompute_travel_times(grid: np.ndarray, stations: pd.DataFrame, vp_km_s: float = 6.0, vs_km_s: float = 6.0 / 1.75):
    station_keys = []
    station_coords = []
    for _, row in stations.iterrows():
        station_keys.append((str(row["network"]).strip(), str(row["station"]).strip()))
        station_coords.append((float(row["longitude"]), float(row["latitude"])))

    station_coords = np.array(station_coords, dtype=float)

    n_grid = len(grid)
    n_sta = len(station_coords)
    tt_p = np.zeros((n_grid, n_sta), dtype=float)
    tt_s = np.zeros((n_grid, n_sta), dtype=float)

    for i, (glon, glat) in enumerate(grid):
        dist = haversine_km(glon, glat, station_coords[:, 0], station_coords[:, 1])
        tt_p[i, :] = dist / vp_km_s
        tt_s[i, :] = dist / vs_km_s

    return station_keys, tt_p, tt_s
```

---

## 6) Associate picks into events by origin-time voting

Why:
- for each candidate source point and each pick, compute a candidate origin time
- real events create dense clusters of similar origin times across stations
- combining P and S phases improves robustness

Decision criteria:
- use a time clustering tolerance around **1.5-3 seconds**
- require at least a few stations, e.g. `>= 4`
- score clusters using:
  - number of unique stations
  - number of P picks
  - number of S picks
  - optional sum of pick confidences

Important:
- count **unique stations**, not just total picks
- prevent one station from dominating with many duplicate picks

### Code: voting-based association

```python
def associate_events_grid(
    picks_df: pd.DataFrame,
    stations: pd.DataFrame,
    grid: np.ndarray,
    vp_km_s: float = 6.0,
    vs_km_s: float = 6.0 / 1.75,
    cluster_tol_s: float = 2.0,
    min_unique_stations: int = 4,
    min_total_picks: int = 5,
):
    station_keys, tt_p, tt_s = precompute_travel_times(grid, stations, vp_km_s=vp_km_s, vs_km_s=vs_km_s)
    sta_index = {k: i for i, k in enumerate(station_keys)}

    work = picks_df.copy()
    work["station_key"] = list(zip(work["network"], work["station"]))
    work = work[work["station_key"].isin(sta_index)].reset_index(drop=True)
    if work.empty:
        return pd.DataFrame(columns=["time", "stations", "p_picks", "s_picks", "score", "grid_lon", "grid_lat"])

    # Numeric seconds relative to earliest pick for stability.
    t0 = work["time"].min()
    work["tsec"] = (work["time"] - t0).dt.total_seconds()

    candidates = []

    for gi, (glon, glat) in enumerate(grid):
        origin_rows = []
        for _, row in work.iterrows():
            si = sta_index[row["station_key"]]
            tt = tt_p[gi, si] if row["phase"] == "P" else tt_s[gi, si]
            origin_sec = row["tsec"] - tt
            origin_rows.append({
                "origin_sec": float(origin_sec),
                "station_key": row["station_key"],
                "phase": row["phase"],
                "pick_time": row["time"],
                "score": float(row["score"]) if pd.notna(row["score"]) else 0.0,
            })

        if not origin_rows:
            continue

        odf = pd.DataFrame(origin_rows).sort_values("origin_sec").reset_index(drop=True)
        vals = odf["origin_sec"].to_numpy()

        start = 0
        n = len(vals)
        while start < n:
            end = start + 1
            while end < n and (vals[end] - vals[start]) <= cluster_tol_s:
                end += 1

            cluster = odf.iloc[start:end].copy()
            unique_stations = cluster["station_key"].nunique()
            total_picks = len(cluster)
            p_picks = int((cluster["phase"] == "P").sum())
            s_picks = int((cluster["phase"] == "S").sum())

            if unique_stations >= min_unique_stations and total_picks >= min_total_picks:
                # Keep best pick per station-phase within cluster.
                cluster = cluster.sort_values(["station_key", "phase", "score"], ascending=[True, True, False])
                cluster = cluster.drop_duplicates(["station_key", "phase"], keep="first")

                # Recompute stats after dedupe.
                unique_stations = cluster["station_key"].nunique()
                total_picks = len(cluster)
                p_picks = int((cluster["phase"] == "P").sum())
                s_picks = int((cluster["phase"] == "S").sum())

                if unique_stations >= min_unique_stations and total_picks >= min_total_picks:
                    origin_est = float(cluster["origin_sec"].median())
                    score = (
                        2.0 * unique_stations
                        + 1.0 * p_picks
                        + 1.0 * s_picks
                        + float(cluster["score"].fillna(0.0).sum())
                    )
                    candidates.append({
                        "time": t0 + pd.to_timedelta(origin_est, unit="s"),
                        "stations": unique_stations,
                        "p_picks": p_picks,
                        "s_picks": s_picks,
                        "score": score,
                        "grid_lon": glon,
                        "grid_lat": glat,
                    })

            start += 1

    if not candidates:
        return pd.DataFrame(columns=["time", "stations", "p_picks", "s_picks", "score", "grid_lon", "grid_lat"])

    cand = pd.DataFrame(candidates).sort_values(["time", "score"], ascending=[True, False]).reset_index(drop=True)
    return cand
```

---

## 7) De-duplicate event hypotheses across grid points

Why:
- the same real event will appear repeatedly from neighboring source grid points
- you must merge these into one event per origin time
- evaluation often treats predictions within a small time window as duplicates

Good rule:
- merge hypotheses within **5 seconds**
- keep the **highest-scoring** candidate in each neighborhood

### Code: merge nearby event candidates

```python
def deduplicate_events(events_df: pd.DataFrame, merge_window_s: float = 5.0) -> pd.DataFrame:
    if events_df.empty:
        return events_df.copy()

    events_df = events_df.sort_values(["time", "score"], ascending=[True, False]).reset_index(drop=True)
    kept = []

    for _, row in events_df.iterrows():
        t = row["time"]
        if not kept:
            kept.append(row)
            continue

        dt = abs((t - kept[-1]["time"]).total_seconds())
        if dt < merge_window_s:
            if row["score"] > kept[-1]["score"]:
                kept[-1] = row
        else:
            kept.append(row)

    out = pd.DataFrame(kept).sort_values("time").reset_index(drop=True)
    return out
```

---

## 8) Finalize timestamps and write the catalog

Why:
- validators are often strict about column names and timestamp formatting
- timezone suffixes can cause mismatches
- sorting by time is expected and easier to inspect

Required output:
- CSV with column `time`
- use ISO format **without timezone**

### Code: write results

```python
def write_results(events_df: pd.DataFrame, output_csv: str):
    out = events_df.copy()
    if "time" not in out.columns:
        raise ValueError("Output DataFrame must contain a 'time' column")

    out = out.sort_values("time").reset_index(drop=True)
    out["time"] = pd.to_datetime(out["time"]).dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str.rstrip("0").str.rstrip(".")
    out.to_csv(output_csv, index=False)
```

---

## 9) Run sanity checks before finalizing

Why:
- many bad catalogs are obviously flawed on inspection:
  - duplicate events within a few seconds
  - unsorted timestamps
  - empty output
  - times outside the waveform span
- a quick check catches formatting and association errors before submission

### Code: sanity-check catalog

```python
def validate_catalog(output_csv: str):
    df = pd.read_csv(output_csv)
    if "time" not in df.columns:
        raise ValueError("results.csv is missing required column 'time'")
    if df.empty:
        raise ValueError("results.csv contains no events")

    t = pd.to_datetime(df["time"])
    if not t.is_monotonic_increasing:
        raise ValueError("Catalog times are not sorted ascending")

    diffs = t.diff().dt.total_seconds().fillna(np.inf)
    near_dups = int((diffs < 5.0).sum())

    print(f"rows={len(df)}")
    print(f"min_time={t.min()}")
    print(f"max_time={t.max()}")
    print(f"duplicates_within_5s={near_dups}")
    print(f"median_interevent_spacing_s={diffs.replace(np.inf, np.nan).median()}")
```

---

# Reference Implementation

The following script is a complete end-to-end implementation. It is designed to be **copy-paste runnable** with minimal modification.

```python
#!/usr/bin/env python3

import warnings
import numpy as np
import pandas as pd
from obspy import read, UTCDateTime
import seisbench.models as sbm


# =========================
# Configuration
# =========================

WAVE_PATH = "/root/data/wave.mseed"
STATION_PATH = "/root/data/stations.csv"
OUTPUT_CSV = "/root/results.csv"

VP_KM_S = 6.0
VS_KM_S = VP_KM_S / 1.75

# Picking thresholds
P_THRESHOLD = 0.30
S_THRESHOLD = 0.30

# Association parameters
GRID_PADDING_DEG = 0.15
GRID_N_LON = 25
GRID_N_LAT = 25
CLUSTER_TOL_S = 2.0
MIN_UNIQUE_STATIONS = 4
MIN_TOTAL_PICKS = 5
DUP_PICK_SEP_S = 1.5
EVENT_MERGE_WINDOW_S = 5.0

EARTH_RADIUS_KM = 6371.0

REQUIRED_STATION_COLUMNS = {
    "network", "station", "channel", "longitude", "latitude", "elevation_m", "response"
}


# =========================
# Utilities
# =========================

def load_station_metadata(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = REQUIRED_STATION_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required station columns: {sorted(missing)}")

    for col in ["network", "station", "channel"]:
        df[col] = df[col].astype(str).str.strip()

    for col in ["longitude", "latitude", "elevation_m", "response"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["network", "station", "channel", "longitude", "latitude"])
    if df.empty:
        raise ValueError("No usable station rows after cleaning")

    rows = []
    for (net, sta), g in df.groupby(["network", "station"], sort=False):
        rows.append({
            "network": net,
            "station": sta,
            "longitude": float(g["longitude"].median()),
            "latitude": float(g["latitude"].median()),
            "elevation_m": float(g["elevation_m"].median()) if g["elevation_m"].notna().any() else 0.0,
        })

    stations = pd.DataFrame(rows).drop_duplicates(["network", "station"]).reset_index(drop=True)
    if stations.empty:
        raise ValueError("No station-level metadata could be derived")
    return stations


def load_stream(path: str):
    st = read(path)
    if len(st) == 0:
        raise ValueError("Waveform stream is empty")
    return st


def build_station_lookup(stations: pd.DataFrame) -> dict:
    lookup = {}
    for _, row in stations.iterrows():
        lookup[(str(row["network"]).strip(), str(row["station"]).strip())] = {
            "longitude": float(row["longitude"]),
            "latitude": float(row["latitude"]),
            "elevation_m": float(row["elevation_m"]),
        }
    return lookup


def _safe_getattr(obj, names, default=None):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
    return default


def load_phasenet_model():
    candidates = [
        ("PhaseNet", "instance"),
        ("PhaseNet", "original"),
        ("PhaseNet", "ethz"),
        ("PhaseNet", None),
    ]
    errors = []
    for model_name, pretrained_name in candidates:
        try:
            ModelCls = getattr(sbm, model_name)
            if pretrained_name is None:
                model = ModelCls.from_pretrained()
            else:
                model = ModelCls.from_pretrained(pretrained_name)
            return model
        except Exception as e:
            errors.append(f"{model_name}/{pretrained_name}: {e}")
    raise RuntimeError("Could not load a PhaseNet pretrained model:\n" + "\n".join(errors))


def classify_picks(model, stream, batch_size=256, p_threshold=0.3, s_threshold=0.3):
    kwargs_list = [
        {"batch_size": batch_size, "P_threshold": p_threshold, "S_threshold": s_threshold},
        {"batch_size": batch_size, "p_threshold": p_threshold, "s_threshold": s_threshold},
        {"batch_size": batch_size},
        {},
    ]
    last_err = None
    for kwargs in kwargs_list:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                picks = model.classify(stream, **kwargs)
            return picks
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Model classify failed for all tested argument patterns: {last_err}")


def picks_to_dataframe(picks, station_lookup: dict) -> pd.DataFrame:
    rows = []
    iterable = picks.picks if hasattr(picks, "picks") else picks

    for pk in iterable:
        network = str(_safe_getattr(pk, ["network"], "")).strip()
        station = str(_safe_getattr(pk, ["station"], "")).strip()
        phase = str(_safe_getattr(pk, ["phase", "phase_hint", "type"], "")).upper().strip()
        if phase not in {"P", "S"}:
            continue

        t = _safe_getattr(pk, ["peak_time", "start_time", "time"])
        if t is None:
            continue

        try:
            t = UTCDateTime(t).datetime
        except Exception:
            continue

        score = _safe_getattr(pk, ["peak_value", "probability", "score"], np.nan)
        try:
            score = float(score)
        except Exception:
            score = np.nan

        key = (network, station)
        if key not in station_lookup:
            continue

        rows.append({
            "network": network,
            "station": station,
            "phase": phase,
            "time": pd.Timestamp(t),
            "score": score,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("No usable P/S picks matched station metadata")

    return df.sort_values(["time", "network", "station", "phase"]).reset_index(drop=True)


def suppress_nearby_duplicate_picks(picks_df: pd.DataFrame, min_sep_seconds: float = 1.5) -> pd.DataFrame:
    if picks_df.empty:
        return picks_df.copy()

    kept = []
    for (net, sta, phase), g in picks_df.groupby(["network", "station", "phase"], sort=False):
        g = g.sort_values(["time", "score"], ascending=[True, False]).reset_index(drop=True)
        accepted = []
        for _, row in g.iterrows():
            t = row["time"]
            if not accepted:
                accepted.append(row)
                continue

            dts = [abs((t - r["time"]).total_seconds()) for r in accepted]
            min_idx = int(np.argmin(dts))
            if dts[min_idx] >= min_sep_seconds:
                accepted.append(row)
            else:
                old_score = accepted[min_idx]["score"]
                new_score = row["score"]
                if pd.notna(new_score) and (pd.isna(old_score) or new_score > old_score):
                    accepted[min_idx] = row

        kept.extend(accepted)

    out = pd.DataFrame(kept)
    return out.sort_values("time").reset_index(drop=True)


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    return EARTH_RADIUS_KM * c


def build_grid_from_stations(stations: pd.DataFrame, padding_deg: float = 0.15, n_lon: int = 25, n_lat: int = 25):
    lon_min = stations["longitude"].min() - padding_deg
    lon_max = stations["longitude"].max() + padding_deg
    lat_min = stations["latitude"].min() - padding_deg
    lat_max = stations["latitude"].max() + padding_deg

    lons = np.linspace(lon_min, lon_max, n_lon)
    lats = np.linspace(lat_min, lat_max, n_lat)
    grid = np.array([(lon, lat) for lat in lats for lon in lons], dtype=float)
    if grid.size == 0:
        raise ValueError("Generated empty grid")
    return grid


def precompute_travel_times(grid: np.ndarray, stations: pd.DataFrame, vp_km_s: float, vs_km_s: float):
    station_keys = []
    station_coords = []

    for _, row in stations.iterrows():
        station_keys.append((str(row["network"]).strip(), str(row["station"]).strip()))
        station_coords.append((float(row["longitude"]), float(row["latitude"])))

    station_coords = np.array(station_coords, dtype=float)
    n_grid = len(grid)
    n_sta = len(station_coords)

    tt_p = np.zeros((n_grid, n_sta), dtype=float)
    tt_s = np.zeros((n_grid, n_sta), dtype=float)

    for i, (glon, glat) in enumerate(grid):
        dist = haversine_km(glon, glat, station_coords[:, 0], station_coords[:, 1])
        tt_p[i, :] = dist / vp_km_s
        tt_s[i, :] = dist / vs_km_s

    return station_keys, tt_p, tt_s


def associate_events_grid(
    picks_df: pd.DataFrame,
    stations: pd.DataFrame,
    grid: np.ndarray,
    vp_km_s: float,
    vs_km_s: float,
    cluster_tol_s: float,
    min_unique_stations: int,
    min_total_picks: int,
):
    station_keys, tt_p, tt_s = precompute_travel_times(grid, stations, vp_km_s, vs_km_s)
    sta_index = {k: i for i, k in enumerate(station_keys)}

    work = picks_df.copy()
    work["station_key"] = list(zip(work["network"], work["station"]))
    work = work[work["station_key"].isin(sta_index)].reset_index(drop=True)
    if work.empty:
        return pd.DataFrame(columns=["time", "stations", "p_picks", "s_picks", "score", "grid_lon", "grid_lat"])

    t0 = work["time"].min()
    work["tsec"] = (work["time"] - t0).dt.total_seconds()

    candidates = []

    for gi, (glon, glat) in enumerate(grid):
        origin_rows = []
        for _, row in work.iterrows():
            si = sta_index[row["station_key"]]
            tt = tt_p[gi, si] if row["phase"] == "P" else tt_s[gi, si]
            origin_rows.append({
                "origin_sec": float(row["tsec"] - tt),
                "station_key": row["station_key"],
                "phase": row["phase"],
                "pick_time": row["time"],
                "score": float(row["score"]) if pd.notna(row["score"]) else 0.0,
            })

        if not origin_rows:
            continue

        odf = pd.DataFrame(origin_rows).sort_values("origin_sec").reset_index(drop=True)
        vals = odf["origin_sec"].to_numpy()

        start = 0
        n = len(vals)
        while start < n:
            end = start + 1
            while end < n and (vals[end] - vals[start]) <= cluster_tol_s:
                end += 1

            cluster = odf.iloc[start:end].copy()
            unique_stations = cluster["station_key"].nunique()
            total_picks = len(cluster)
            p_picks = int((cluster["phase"] == "P").sum())
            s_picks = int((cluster["phase"] == "S").sum())

            if unique_stations >= min_unique_stations and total_picks >= min_total_picks:
                cluster = cluster.sort_values(["station_key", "phase", "score"], ascending=[True, True, False])
                cluster = cluster.drop_duplicates(["station_key", "phase"], keep="first")

                unique_stations = cluster["station_key"].nunique()
                total_picks = len(cluster)
                p_picks = int((cluster["phase"] == "P").sum())
                s_picks = int((cluster["phase"] == "S").sum())

                if unique_stations >= min_unique_stations and total_picks >= min_total_picks:
                    origin_est = float(cluster["origin_sec"].median())
                    score = (
                        2.0 * unique_stations
                        + 1.0 * p_picks
                        + 1.0 * s_picks
                        + float(cluster["score"].fillna(0.0).sum())
                    )
                    candidates.append({
                        "time": t0 + pd.to_timedelta(origin_est, unit="s"),
                        "stations": unique_stations,
                        "p_picks": p_picks,
                        "s_picks": s_picks,
                        "score": score,
                        "grid_lon": float(glon),
                        "grid_lat": float(glat),
                    })

            start += 1

    if not candidates:
        return pd.DataFrame(columns=["time", "stations", "p_picks", "s_picks", "score", "grid_lon", "grid_lat"])

    cand = pd.DataFrame(candidates)
    return cand.sort_values(["time", "score"], ascending=[True, False]).reset_index(drop=True)


def deduplicate_events(events_df: pd.DataFrame, merge_window_s: float = 5.0) -> pd.DataFrame:
    if events_df.empty:
        return events_df.copy()

    events_df = events_df.sort_values(["time", "score"], ascending=[True, False]).reset_index(drop=True)
    kept = []

    for _, row in events_df.iterrows():
        t = row["time"]
        if not kept:
            kept.append(row)
            continue

        dt = abs((t - kept[-1]["time"]).total_seconds())
        if dt < merge_window_s:
            if row["score"] > kept[-1]["score"]:
                kept[-1] = row
        else:
            kept.append(row)

    return pd.DataFrame(kept).sort_values("time").reset_index(drop=True)


def write_results(events_df: pd.DataFrame, output_csv: str):
    out = events_df.copy()
    if "time" not in out.columns:
        raise ValueError("Output DataFrame must contain 'time'")

    out = out.sort_values("time").reset_index(drop=True)
    out["time"] = pd.to_datetime(out["time"]).dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str.rstrip("0").str.rstrip(".")
    out.to_csv(output_csv, index=False)


def validate_catalog(output_csv: str):
    df = pd.read_csv(output_csv)
    if "time" not in df.columns:
        raise ValueError("Missing required 'time' column")
    if df.empty:
        raise ValueError("Catalog is empty")

    t = pd.to_datetime(df["time"])
    diffs = t.diff().dt.total_seconds().fillna(np.inf)

    print("Catalog summary")
    print("--------------")
    print(f"rows: {len(df)}")
    print(f"min time: {t.min()}")
    print(f"max time: {t.max()}")
    print(f"duplicates within 5 s: {int((diffs < 5.0).sum())}")
    print(f"median inter-event spacing s: {diffs.replace(np.inf, np.nan).median()}")


def main():
    # 1) Load data
    stations = load_station_metadata(STATION_PATH)
    station_lookup = build_station_lookup(stations)
    stream = load_stream(WAVE_PATH)

    # 2) Pick phases using SeisBench
    model = load_phasenet_model()
    picks = classify_picks(
        model,
        stream,
        batch_size=256,
        p_threshold=P_THRESHOLD,
        s_threshold=S_THRESHOLD,
    )

    picks_df = picks_to_dataframe(picks, station_lookup)
    picks_df = suppress_nearby_duplicate_picks(picks_df, min_sep_seconds=DUP_PICK_SEP_S)

    if picks_df.empty:
        raise RuntimeError("All picks were filtered out; cannot associate events")

    # 3) Build source grid
    grid = build_grid_from_stations(
        stations,
        padding_deg=GRID_PADDING_DEG,
        n_lon=GRID_N_LON,
        n_lat=GRID_N_LAT,
    )

    # 4) Associate picks into events
    events = associate_events_grid(
        picks_df=picks_df,
        stations=stations,
        grid=grid,
        vp_km_s=VP_KM_S,
        vs_km_s=VS_KM_S,
        cluster_tol_s=CLUSTER_TOL_S,
        min_unique_stations=MIN_UNIQUE_STATIONS,
        min_total_picks=MIN_TOTAL_PICKS,
    )

    events = deduplicate_events(events, merge_window_s=EVENT_MERGE_WINDOW_S)
    if events.empty:
        raise RuntimeError("No events survived association")

    # 5) Write output
    write_results(events, OUTPUT_CSV)
    validate_catalog(OUTPUT_CSV)


if __name__ == "__main__":
    main()
```

---

# Parameter Tuning Guidance

These tasks often hinge on the **precision/recall balance**.

## If recall is too low
Symptoms:
- too few events
- many long quiet periods despite obvious activity
- picks exist but few associations survive

Try:
- lower `P_THRESHOLD` / `S_THRESHOLD` modestly, e.g. from `0.30` to `0.20`
- reduce `MIN_UNIQUE_STATIONS`
- reduce `MIN_TOTAL_PICKS`
- slightly widen `CLUSTER_TOL_S`

## If precision is too low
Symptoms:
- many events packed unrealistically close together
- lots of one-off weak events
- hidden-score F1 likely hurt by duplicates/false positives

Try:
- increase picker thresholds
- increase `MIN_UNIQUE_STATIONS`
- increase `MIN_TOTAL_PICKS`
- tighten deduplication and cluster windows
- require stronger event scores before writing

### Code: optional post-filter by score

```python
def filter_weak_events(events_df: pd.DataFrame, min_score: float = 10.0) -> pd.DataFrame:
    if events_df.empty:
        return events_df.copy()
    return events_df[events_df["score"] >= min_score].sort_values("time").reset_index(drop=True)
```

---

# Practical Verification Checklist

Before final submission, confirm:

1. `/root/results.csv` exists
2. it has a `time` column
3. timestamps are ISO-like and timezone-free
4. rows are sorted by time
5. there are no duplicate events within 5 seconds
6. event times lie inside or near the waveform time span
7. event count is plausible for the duration and regional activity level

### Code: quick shell-friendly check

```python
import pandas as pd

df = pd.read_csv("/root/results.csv")
t = pd.to_datetime(df["time"])
print("rows", len(df))
print("min", t.min(), "max", t.max())
print("duplicates<5s", int((t.diff().dt.total_seconds().fillna(999) < 5).sum()))
print("median spacing", t.diff().dt.total_seconds().median())
```

---

# Common Pitfalls

## 1) Treating station CSV rows as station-level metadata
Each row is often **one channel**, not one station.  
If you associate using channel rows directly, you can overcount support and bias scoring.

**Fix:** collapse to one row per `(network, station)` before association.

---

## 2) Matching picks by channel instead of station
SeisBench may pick on multiple components for the same station.  
If you do not merge by station, one physical station can appear to support an event multiple times.

**Fix:** associate at `(network, station)` granularity and deduplicate per station-phase.

---

## 3) Forgetting to remove nearby duplicate picks
Deep models may produce multiple close picks on the same station/phase around one arrival.

**Fix:** suppress duplicate picks within a short window, keeping the strongest score.

---

## 4) Using raw degree differences as distance
Longitude/latitude are angular coordinates, not kilometers.

**Fix:** use haversine or another geodesic approximation for travel times.

---

## 5) Emitting multiple event hypotheses for the same earthquake
Grid search naturally creates repeated origin-time candidates around the true source.

**Fix:** merge candidate events within a few seconds and keep the strongest one.

---

## 6) Writing timestamps with timezone suffixes or inconsistent formatting
Validators may parse times strictly.

**Fix:** write ISO strings without timezone, e.g. `YYYY-MM-DDTHH:MM:SS.sss`.

---

## 7) Overfitting to a single station or phase type
A catalog built from mostly one station or only sparse P picks often becomes noisy.

**Fix:** require multiple unique stations and score events using both station count and phase count.

---

## 8) Finalizing without sanity checks
A catalog can look valid structurally but still fail because of:
- duplicates within the matching tolerance
- unsorted times
- implausible event rate

**Fix:** always inspect row count, time range, duplicate count, and median spacing.

---

# Notes on Environment and Libraries

Useful packages commonly available in this task family:
- `seisbench==0.10.2`
- `obspy`
- `pandas`
- `numpy`

Recommended stack:
- waveform IO: `obspy`
- deep picking: `seisbench.models.PhaseNet`
- data wrangling: `pandas`
- association logic: custom `numpy`/`pandas`

You do **not** need a full earthquake relocation package when the evaluation target is just **event time**. A clean picking pipeline plus a robust grid-voting association is often sufficient and much easier to debug.

---

# Minimal Output Contract

At minimum, produce:

```csv
time
2019-07-04T19:00:09.480
2019-07-04T19:01:09.934
...
```

Extra columns are fine, for example:

```csv
time,stations,p_picks,s_picks,score
2019-07-04T19:00:09.480,13,13,9,34.045
```

As long as `time` is present and correct, downstream evaluation can ignore the rest.

---

# Recommended Execution Path Summary

If you need a reliable default approach for similar tasks, do this:

1. Read station CSV and collapse per-channel rows to station-level coordinates.
2. Read miniSEED with ObsPy.
3. Use SeisBench **PhaseNet** pretrained weights to pick P and S arrivals.
4. Convert picks into a clean table keyed by `(network, station, phase, time, score)`.
5. Remove duplicate near-identical picks on the same station-phase.
6. Build a modest spatial grid around the station network.
7. For each grid point, convert picks to candidate origin times using `vp` and `vs`.
8. Find dense origin-time clusters with support from multiple unique stations.
9. Score and deduplicate event hypotheses across neighboring grid points.
10. Write sorted ISO timestamps to `results.csv` and verify no duplicates within 5 seconds.

This pattern is simple, transparent, and strong for event-time catalog tasks where waveform picking quality is more important than precise location inversion.