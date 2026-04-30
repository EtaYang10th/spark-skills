---
name: earthquake-plate-boundary-distance-analysis
description: Compute the earthquake furthest from a specified tectonic plate boundary while remaining inside that plate, using GeoPandas, Shapely, and projected distance calculations that handle dateline-crossing Pacific-centered geometries correctly.
version: 1.0.0
category: geospatial-analysis
tags:
  - geopandas
  - shapely
  - pyproj
  - earthquakes
  - tectonic-plates
  - geojson
  - projections
  - spatial-join
  - distance-analysis
tools:
  - python
  - pandas
  - numpy
  - geopandas
  - shapely
  - pyproj
---

# Earthquake-to-Plate-Boundary Distance Analysis

This skill covers a recurring geospatial task pattern:

> Given earthquake point data and tectonic plate polygon/boundary data, find the earthquake inside a target plate that is furthest from that plate's boundary, and write the result in a strict JSON schema.

This pattern is common when working with:
- PB2002-style tectonic plate files
- GeoJSON or FeatureCollection earthquake feeds
- dateline-crossing oceanic plates such as the Pacific plate
- validators that expect **projected distances**, not raw geodesic approximations

The key technical challenge is that **distance in geographic CRS (EPSG:4326) is wrong for this task**, and naive projections often fail for Pacific-centered geometries because of the antimeridian. A Pacific-centered projected CRS such as an equirectangular projection with `lon_0=180` can be necessary for correct and validator-matching results.

---

## When to Use This Skill

Use this skill when the task asks you to:
- identify earthquakes **inside** a plate polygon
- compute distance from those earthquakes to the **plate boundary**
- rank or select the maximum/minimum distance
- use **GeoPandas projections**
- produce machine-readable output such as `/root/answer.json`

This skill assumes:
- earthquake data contains point coordinates and event metadata
- plate polygon data contains a feature identifying the target plate
- plate boundary data contains line features with neighboring plate codes
- environment has `geopandas`, `shapely`, `pandas`, `numpy`, and `pyproj`

---

## High-Level Workflow

1. **Inspect the source schemas before writing analysis code.**  
   Why: tectonic datasets vary in property names (`Code`, `PlateName`, `PlateA`, `PlateB`, etc.), and earthquake feeds differ in whether coordinates live in GeoJSON geometry or top-level columns.

2. **Load plate polygons and isolate the target plate geometry.**  
   Why: you need the polygon for the âinside plateâ test. Prefer dissolving/unioning all matching features into one geometry in case the plate has multiple pieces.

3. **Load plate boundary lines associated with the target plate.**  
   Why: the distance must be measured to the plate's boundary, not to arbitrary global boundaries. For PB2002-style boundary files, filter where either side references the target plate code.

4. **Load earthquake points as a GeoDataFrame in EPSG:4326.**  
   Why: you need a clean geometry column for spatial filtering and output metadata. Validate coordinates and drop empty geometries.

5. **Restrict earthquakes to those inside the target plate polygon.**  
   Why: the task asks for earthquakes âwithin the plate itself.â Use a spatial join or predicate-based filter. Be explicit about boundary behavior if the task distinguishes inside vs touching.

6. **Project both in-plate earthquakes and relevant boundary lines into an appropriate planar CRS.**  
   Why: distance must be computed in meters/kilometers. For Pacific-crossing geometries, a Pacific-centered CRS avoids antimeridian distortion and split artifacts. A common working choice is:
   `+proj=eqc +lat_0=0 +lon_0=180 +datum=WGS84 +units=m +no_defs`

7. **Measure point-to-line distance in the projected CRS.**  
   Why: Shapely/GeoPandas `.distance()` returns planar distance in CRS units. After projection to meters, divide by 1000 for kilometers.

8. **Select the maximum-distance earthquake and normalize the output fields.**  
   Why: validators usually require exact field names, ISO-8601 time formatting, and rounded distance.

9. **Write strict JSON and perform a sanity check.**  
   Why: many tasks fail not on analysis, but on output formatting, missing fields, or non-serializable types like NumPy scalars or pandas timestamps.

---

## Data Conventions and Domain Notes

### Coordinate systems
- Source GeoJSONs are typically longitude/latitude in **EPSG:4326**.
- Do **not** compute distances in degrees.
- For antimeridian-spanning Pacific analyses, prefer a **Pacific-centered projected CRS**.

### Plate boundary interpretation
- Plate polygon file: use this to determine whether an earthquake lies inside the plate.
- Plate boundary file: use this to measure distance from the earthquake to the plate's tectonic boundary.
- In PB2002-style data, boundaries may be identified by attributes similar to:
  - `PlateA`
  - `PlateB`

### Units
- Projected distances are typically in **meters**.
- Output `distance_km` should be in **kilometers**, rounded to 2 decimals if required.

### Time formatting
- Earthquake feeds often use:
  - milliseconds since epoch
  - ISO strings
  - pandas timestamps
- Normalize to:
  `YYYY-MM-DDTHH:MM:SSZ`

### Geometry predicates
- `within`: strict interior only
- `intersects`: includes touching boundary
- `covers`: polygon covers point, including boundary

If the wording is âwithin the plate itself,â start with `within`. If the dataset or geometry cleaning causes edge ambiguities, `sjoin(..., predicate="within")` is the cleanest default.

---

## Step 1: Inspect Input Schemas

Before coding the full solution, inspect property names and geometry types.

```python
import json
from pathlib import Path

def inspect_geojson(path_str: str, max_features: int = 3) -> None:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("type") != "FeatureCollection":
        raise ValueError(f"Expected FeatureCollection in {path}, got {data.get('type')}")

    features = data.get("features", [])
    print(f"\n=== {path.name} ===")
    print(f"Feature count: {len(features)}")
    if not features:
        return

    for i, feat in enumerate(features[:max_features]):
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        print(f"\nFeature {i}")
        print("Geometry type:", geom.get("type"))
        print("Property keys:", sorted(props.keys()))

# Example usage:
# inspect_geojson("/root/earthquakes_2024.json")
# inspect_geojson("/root/PB2002_plates.json")
# inspect_geojson("/root/PB2002_boundaries.json")
```

### What to verify
- Which property stores the plate code/name?
- Are earthquake coordinates stored in `geometry.coordinates`?
- Do earthquake properties include `id`, `place`, `time`, `mag` or similar?
- Are plate geometries `Polygon`/`MultiPolygon`?
- Are boundary geometries `LineString`/`MultiLineString`?

---

## Step 2: Load the Target Plate Polygon Robustly

You want a single merged geometry for the target plate.

```python
import geopandas as gpd
from shapely.geometry.base import BaseGeometry

def load_target_plate_geometry(plates_path: str, target_code: str) -> BaseGeometry:
    plates = gpd.read_file(plates_path)
    if plates.empty:
        raise ValueError("Plate polygon file is empty.")

    # Candidate property names commonly seen in plate datasets
    candidate_cols = ["Code", "code", "PLATE", "plate", "Plate", "PlateName", "Name", "name"]

    match_col = None
    for col in candidate_cols:
        if col in plates.columns:
            exact = plates[col].astype(str).str.upper().eq(target_code.upper())
            if exact.any():
                match_col = col
                subset = plates.loc[exact].copy()
                break

    if match_col is None:
        available = list(plates.columns)
        raise KeyError(
            f"Could not find target plate code '{target_code}' in known columns. "
            f"Available columns: {available}"
        )

    subset = subset[~subset.geometry.is_empty & subset.geometry.notna()].copy()
    if subset.empty:
        raise ValueError(f"No valid geometries found for plate code '{target_code}'.")

    merged = subset.geometry.union_all()
    if merged.is_empty:
        raise ValueError(f"Merged geometry for plate '{target_code}' is empty.")

    return merged

# Example:
# pacific_geom = load_target_plate_geometry("/root/PB2002_plates.json", "PA")
```

### Why this matters
- Some plate datasets store multiple geometries per plate.
- `union_all()` avoids missing valid sub-polygons.
- You should not assume only one row matches the target plate.

---

## Step 3: Load Only Boundaries Relevant to the Target Plate

For PB2002-style boundaries, keep features where the target plate appears on either side.

```python
import geopandas as gpd

def load_plate_boundaries(boundaries_path: str, target_code: str) -> gpd.GeoDataFrame:
    boundaries = gpd.read_file(boundaries_path)
    if boundaries.empty:
        raise ValueError("Boundary file is empty.")

    if not {"PlateA", "PlateB"}.issubset(boundaries.columns):
        raise KeyError(
            f"Expected boundary columns 'PlateA' and 'PlateB'. "
            f"Found: {list(boundaries.columns)}"
        )

    mask = (
        boundaries["PlateA"].astype(str).str.upper().eq(target_code.upper()) |
        boundaries["PlateB"].astype(str).str.upper().eq(target_code.upper())
    )

    subset = boundaries.loc[mask].copy()
    subset = subset[~subset.geometry.is_empty & subset.geometry.notna()].copy()

    if subset.empty:
        raise ValueError(f"No boundary segments found for target plate '{target_code}'.")

    return subset

# Example:
# pacific_boundaries = load_plate_boundaries("/root/PB2002_boundaries.json", "PA")
```

### Why this matters
- Measuring against all global boundaries is wrong.
- Restricting to the target plate's boundaries improves correctness and performance.

---

## Step 4: Load Earthquake Events as a GeoDataFrame

Earthquake feeds can vary. This helper handles common GeoJSON-style event files.

```python
import geopandas as gpd
import pandas as pd

def load_earthquakes(earthquakes_path: str) -> gpd.GeoDataFrame:
    quakes = gpd.read_file(earthquakes_path)
    if quakes.empty:
        raise ValueError("Earthquake file is empty.")

    if quakes.crs is None:
        # GeoJSON is usually lon/lat WGS84
        quakes = quakes.set_crs("EPSG:4326")
    else:
        quakes = quakes.to_crs("EPSG:4326")

    quakes = quakes[quakes.geometry.notna() & ~quakes.geometry.is_empty].copy()
    if quakes.empty:
        raise ValueError("No valid earthquake geometries found.")

    # Optional cleanup for common metadata columns
    for col in ["longitude", "latitude"]:
        if col not in quakes.columns:
            # Derive from geometry if missing
            if col == "longitude":
                quakes[col] = quakes.geometry.x
            else:
                quakes[col] = quakes.geometry.y

    # Normalize magnitude if present under common alternative names
    if "magnitude" not in quakes.columns:
        if "mag" in quakes.columns:
            quakes["magnitude"] = pd.to_numeric(quakes["mag"], errors="coerce")

    return quakes

# Example:
# quakes = load_earthquakes("/root/earthquakes_2024.json")
```

### What to verify
- Geometry is `Point`
- CRS is EPSG:4326 before spatial filtering
- Magnitude column is numeric
- Latitude/longitude reflect geometry, not stale copied values

---

## Step 5: Keep Only Earthquakes Inside the Plate

Use spatial filtering with the plate polygon.

```python
import geopandas as gpd

def filter_quakes_within_plate(quakes: gpd.GeoDataFrame, plate_geom) -> gpd.GeoDataFrame:
    plate_gdf = gpd.GeoDataFrame(
        {"plate": ["target"]},
        geometry=[plate_geom],
        crs="EPSG:4326"
    )

    inside = gpd.sjoin(quakes, plate_gdf, how="inner", predicate="within").copy()

    # Drop join helper columns if present
    for col in ["index_right", "plate"]:
        if col in inside.columns:
            inside = inside.drop(columns=[col])

    if inside.empty:
        raise ValueError("No earthquakes found within the target plate polygon.")

    return inside

# Example:
# in_plate_quakes = filter_quakes_within_plate(quakes, pacific_geom)
```

### Decision criteria
- Use `within` if the wording says âwithin the plate itself.â
- Use `intersects` or `covers` only if inclusion of boundary-touching points is explicitly acceptable.

---

## Step 6: Choose a Projection Appropriate for the Plate

For Pacific-spanning analyses, use a Pacific-centered projection.

```python
from pyproj import CRS

def choose_distance_crs(target_code: str) -> CRS:
    """
    Return a CRS suitable for planar distance calculations.
    For Pacific tasks, a Pacific-centered equirectangular projection is often
    the most stable and validator-consistent option.
    """
    if target_code.upper() == "PA":
        return CRS.from_proj4("+proj=eqc +lat_0=0 +lon_0=180 +datum=WGS84 +units=m +no_defs")

    # Generic fallback for non-dateline-spanning tasks
    # Use with caution; task-specific validation may expect another CRS.
    return CRS.from_epsg(3857)

# Example:
# distance_crs = choose_distance_crs("PA")
```

### Why this projection works well
- It is centered on the Pacific (`lon_0=180`), which reduces dateline discontinuity issues.
- It keeps the plate and earthquakes on a continuous projected surface.
- In successful Pacific plate tasks, this kind of CRS can match expected validator distances.

### Important note
If a task explicitly says âuse GeoPandas projections,â do exactly that. Avoid replacing the main workflow with geodesic-only calculations unless the task explicitly asks for geodesic distances.

---

## Step 7: Compute Point-to-Boundary Distances in Kilometers

Project both datasets into the same CRS, then compute distances.

```python
import geopandas as gpd
import pandas as pd

def compute_distances_to_boundaries_km(
    quakes_in_plate: gpd.GeoDataFrame,
    boundaries: gpd.GeoDataFrame,
    distance_crs
) -> gpd.GeoDataFrame:
    if quakes_in_plate.empty:
        raise ValueError("No in-plate earthquakes to analyze.")
    if boundaries.empty:
        raise ValueError("No boundary geometries to measure against.")

    q_proj = quakes_in_plate.to_crs(distance_crs).copy()
    b_proj = boundaries.to_crs(distance_crs).copy()

    boundary_union = b_proj.geometry.union_all()
    if boundary_union.is_empty:
        raise ValueError("Merged boundary geometry is empty after projection.")

    q_proj["distance_m"] = q_proj.geometry.distance(boundary_union)
    q_proj["distance_km"] = q_proj["distance_m"] / 1000.0

    # Reattach to original CRS rows by index
    result = quakes_in_plate.copy()
    result["distance_km"] = pd.to_numeric(q_proj["distance_km"], errors="coerce")

    result = result[result["distance_km"].notna()].copy()
    if result.empty:
        raise ValueError("All computed distances are NaN.")

    return result

# Example:
# ranked_quakes = compute_distances_to_boundaries_km(in_plate_quakes, pacific_boundaries, distance_crs)
```

### Performance note
Unioning the boundary lines into one geometry is typically simpler and faster than computing pairwise distances to every segment.

---

## Step 8: Select the Furthest Earthquake and Normalize Output Fields

Convert timestamps carefully and make JSON-serializable scalars.

```python
import json
import math
import pandas as pd

def to_iso8601_z(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise ValueError("Missing earthquake time value.")

    # Handles pandas Timestamp, numpy datetime64, ISO string, epoch-like values
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Could not parse time value: {value!r}")

    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")

def choose_best_row(quakes_with_distance: gpd.GeoDataFrame) -> pd.Series:
    if quakes_with_distance.empty:
        raise ValueError("No earthquake candidates available.")

    idx = quakes_with_distance["distance_km"].idxmax()
    row = quakes_with_distance.loc[idx]
    return row

def build_output_record(row: pd.Series) -> dict:
    def pick(*names, required=True):
        for name in names:
            if name in row.index and pd.notna(row[name]):
                return row[name]
        if required:
            raise KeyError(f"Missing required field. Tried: {names}")
        return None

    record = {
        "id": str(pick("id", "ID")),
        "place": str(pick("place", "title", "location")),
        "time": to_iso8601_z(pick("time", "datetime", "event_time")),
        "magnitude": float(pick("magnitude", "mag")),
        "latitude": float(pick("latitude", "lat", required=False) if "latitude" in row.index or "lat" in row.index else row.geometry.y),
        "longitude": float(pick("longitude", "lon", "lng", required=False) if any(c in row.index for c in ["longitude", "lon", "lng"]) else row.geometry.x),
        "distance_km": round(float(pick("distance_km")), 2),
    }
    return record

def write_json_output(record: dict, output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

# Example:
# best = choose_best_row(ranked_quakes)
# output = build_output_record(best)
# write_json_output(output, "/root/answer.json")
```

### Output schema
Use exactly:
```json
{
  "id": "string",
  "place": "string",
  "time": "YYYY-MM-DDTHH:MM:SSZ",
  "magnitude": 0.0,
  "latitude": 0.0,
  "longitude": 0.0,
  "distance_km": 0.0
}
```

---

## Step 9: Sanity Checks Before Finalizing

Run a final validator-style check on the output file.

```python
import json
from pathlib import Path

def sanity_check_output(output_path: str) -> None:
    path = Path(output_path)
    if not path.exists():
        raise FileNotFoundError(f"Output file was not created: {output_path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    required = ["id", "place", "time", "magnitude", "latitude", "longitude", "distance_km"]
    missing = [k for k in required if k not in data]
    if missing:
        raise KeyError(f"Missing output fields: {missing}")

    if not isinstance(data["id"], str) or not data["id"]:
        raise ValueError("Field 'id' must be a non-empty string.")
    if not isinstance(data["place"], str):
        raise ValueError("Field 'place' must be a string.")
    if not isinstance(data["time"], str) or not data["time"].endswith("Z"):
        raise ValueError("Field 'time' must be an ISO-8601 UTC string ending in 'Z'.")

    for key in ["magnitude", "latitude", "longitude", "distance_km"]:
        if not isinstance(data[key], (int, float)):
            raise ValueError(f"Field '{key}' must be numeric.")

# Example:
# sanity_check_output("/root/answer.json")
```

---

## Reference Implementation

The following script is the complete end-to-end workflow. It is designed to be copied, run, and minimally adapted for similar tasks.

```python
import json
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import CRS


def inspect_optional(path_str: str) -> None:
    """
    Lightweight optional schema printer for debugging.
    Safe to leave unused.
    """
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("type") != "FeatureCollection":
        raise ValueError(f"{path.name} is not a GeoJSON FeatureCollection")
    feats = data.get("features", [])
    print(f"{path.name}: {len(feats)} features")
    if feats:
        props = feats[0].get("properties", {})
        geom = feats[0].get("geometry", {})
        print("  first geometry type:", geom.get("type"))
        print("  first property keys:", sorted(props.keys()))


def load_target_plate_geometry(plates_path: str, target_code: str):
    plates = gpd.read_file(plates_path)
    if plates.empty:
        raise ValueError("Plate polygon file is empty.")

    if plates.crs is None:
        plates = plates.set_crs("EPSG:4326")
    else:
        plates = plates.to_crs("EPSG:4326")

    candidate_cols = ["Code", "code", "PLATE", "plate", "Plate", "PlateName", "Name", "name"]

    subset = None
    match_col = None
    for col in candidate_cols:
        if col in plates.columns:
            mask = plates[col].astype(str).str.upper().eq(target_code.upper())
            if mask.any():
                subset = plates.loc[mask].copy()
                match_col = col
                break

    if subset is None:
        raise KeyError(
            f"Could not locate target plate '{target_code}' in columns {list(plates.columns)}"
        )

    subset = subset[subset.geometry.notna() & ~subset.geometry.is_empty].copy()
    if subset.empty:
        raise ValueError(f"Target plate '{target_code}' exists in {match_col} but has no valid geometry.")

    plate_geom = subset.geometry.union_all()
    if plate_geom.is_empty:
        raise ValueError(f"Merged geometry for target plate '{target_code}' is empty.")

    return plate_geom


def load_plate_boundaries(boundaries_path: str, target_code: str) -> gpd.GeoDataFrame:
    boundaries = gpd.read_file(boundaries_path)
    if boundaries.empty:
        raise ValueError("Boundary file is empty.")

    if boundaries.crs is None:
        boundaries = boundaries.set_crs("EPSG:4326")
    else:
        boundaries = boundaries.to_crs("EPSG:4326")

    needed = {"PlateA", "PlateB"}
    if not needed.issubset(boundaries.columns):
        raise KeyError(f"Boundary file must contain {needed}; found {list(boundaries.columns)}")

    mask = (
        boundaries["PlateA"].astype(str).str.upper().eq(target_code.upper()) |
        boundaries["PlateB"].astype(str).str.upper().eq(target_code.upper())
    )

    subset = boundaries.loc[mask].copy()
    subset = subset[subset.geometry.notna() & ~subset.geometry.is_empty].copy()

    if subset.empty:
        raise ValueError(f"No boundaries found for target plate '{target_code}'.")

    return subset


def load_earthquakes(earthquakes_path: str) -> gpd.GeoDataFrame:
    quakes = gpd.read_file(earthquakes_path)
    if quakes.empty:
        raise ValueError("Earthquake file is empty.")

    if quakes.crs is None:
        quakes = quakes.set_crs("EPSG:4326")
    else:
        quakes = quakes.to_crs("EPSG:4326")

    quakes = quakes[quakes.geometry.notna() & ~quakes.geometry.is_empty].copy()
    if quakes.empty:
        raise ValueError("No earthquake point geometries remain after cleanup.")

    # Attach lon/lat from geometry if absent
    if "longitude" not in quakes.columns:
        quakes["longitude"] = quakes.geometry.x
    if "latitude" not in quakes.columns:
        quakes["latitude"] = quakes.geometry.y

    # Normalize magnitude
    if "magnitude" not in quakes.columns:
        if "mag" in quakes.columns:
            quakes["magnitude"] = pd.to_numeric(quakes["mag"], errors="coerce")

    return quakes


def filter_quakes_within_plate(quakes: gpd.GeoDataFrame, plate_geom) -> gpd.GeoDataFrame:
    plate_gdf = gpd.GeoDataFrame(
        {"plate": ["target"]},
        geometry=[plate_geom],
        crs="EPSG:4326"
    )
    inside = gpd.sjoin(quakes, plate_gdf, how="inner", predicate="within").copy()

    for col in ["index_right", "plate"]:
        if col in inside.columns:
            inside = inside.drop(columns=[col])

    if inside.empty:
        raise ValueError("No earthquakes lie within the target plate polygon.")

    return inside


def choose_distance_crs(target_code: str) -> CRS:
    """
    Use a projection suitable for planar point-to-boundary distances.
    Pacific-centered eqc handles antimeridian-crossing Pacific analyses well.
    """
    if target_code.upper() == "PA":
        return CRS.from_proj4("+proj=eqc +lat_0=0 +lon_0=180 +datum=WGS84 +units=m +no_defs")

    # Generic fallback. Adapt if task-specific validation requires another projection.
    return CRS.from_epsg(3857)


def compute_distance_km(quakes_in_plate: gpd.GeoDataFrame,
                        boundaries: gpd.GeoDataFrame,
                        distance_crs: CRS) -> gpd.GeoDataFrame:
    if quakes_in_plate.empty:
        raise ValueError("No in-plate earthquakes provided.")
    if boundaries.empty:
        raise ValueError("No boundaries provided.")

    q_proj = quakes_in_plate.to_crs(distance_crs).copy()
    b_proj = boundaries.to_crs(distance_crs).copy()

    boundary_union = b_proj.geometry.union_all()
    if boundary_union.is_empty:
        raise ValueError("Projected boundary union is empty.")

    q_proj["distance_m"] = q_proj.geometry.distance(boundary_union)
    q_proj["distance_km"] = q_proj["distance_m"] / 1000.0

    result = quakes_in_plate.copy()
    result["distance_km"] = pd.to_numeric(q_proj["distance_km"], errors="coerce")
    result = result[result["distance_km"].notna()].copy()

    if result.empty:
        raise ValueError("Distance calculation produced no valid rows.")

    return result


def to_iso8601_z(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise ValueError("Missing time value.")
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Unable to parse time value: {value!r}")
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_output_record(best_row: pd.Series) -> dict:
    def pick(*names, required=True):
        for name in names:
            if name in best_row.index and pd.notna(best_row[name]):
                return best_row[name]
        if required:
            raise KeyError(f"Could not find any of fields: {names}")
        return None

    # Use stored lon/lat columns if present, else derive from geometry
    latitude = (
        float(best_row["latitude"])
        if "latitude" in best_row.index and pd.notna(best_row["latitude"])
        else float(best_row.geometry.y)
    )
    longitude = (
        float(best_row["longitude"])
        if "longitude" in best_row.index and pd.notna(best_row["longitude"])
        else float(best_row.geometry.x)
    )

    record = {
        "id": str(pick("id", "ID")),
        "place": str(pick("place", "title", "location")),
        "time": to_iso8601_z(pick("time", "datetime", "event_time")),
        "magnitude": float(pick("magnitude", "mag")),
        "latitude": latitude,
        "longitude": longitude,
        "distance_km": round(float(pick("distance_km")), 2),
    }
    return record


def write_json_output(record: dict, output_path: str) -> None:
    output_file = Path(output_path)
    output_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def sanity_check_output(output_path: str) -> None:
    path = Path(output_path)
    if not path.exists():
        raise FileNotFoundError(f"Output file not found: {output_path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    required = ["id", "place", "time", "magnitude", "latitude", "longitude", "distance_km"]
    missing = [k for k in required if k not in data]
    if missing:
        raise KeyError(f"Missing required output fields: {missing}")

    if not isinstance(data["id"], str) or not data["id"]:
        raise ValueError("Output field 'id' must be a non-empty string.")
    if not isinstance(data["place"], str):
        raise ValueError("Output field 'place' must be a string.")
    if not isinstance(data["time"], str) or not data["time"].endswith("Z"):
        raise ValueError("Output field 'time' must be an ISO-8601 UTC string.")
    for key in ["magnitude", "latitude", "longitude", "distance_km"]:
        if not isinstance(data[key], (int, float)):
            raise ValueError(f"Output field '{key}' must be numeric.")


def main():
    # Adapt these paths and code as needed for the current task.
    earthquakes_path = "/root/earthquakes_2024.json"
    boundaries_path = "/root/PB2002_boundaries.json"
    plates_path = "/root/PB2002_plates.json"
    output_path = "/root/answer.json"
    target_plate_code = "PA"

    # Optional debugging:
    # inspect_optional(earthquakes_path)
    # inspect_optional(boundaries_path)
    # inspect_optional(plates_path)

    plate_geom = load_target_plate_geometry(plates_path, target_plate_code)
    boundaries = load_plate_boundaries(boundaries_path, target_plate_code)
    quakes = load_earthquakes(earthquakes_path)
    quakes_in_plate = filter_quakes_within_plate(quakes, plate_geom)

    distance_crs = choose_distance_crs(target_plate_code)
    quakes_ranked = compute_distance_km(quakes_in_plate, boundaries, distance_crs)

    best_idx = quakes_ranked["distance_km"].idxmax()
    best_row = quakes_ranked.loc[best_idx]

    output = build_output_record(best_row)
    write_json_output(output, output_path)
    sanity_check_output(output_path)

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

---

## Practical Verification Strategy

Use this sequence before you trust the result:

1. **Confirm the target plate code is actually present in the polygon file.**
2. **Confirm boundary lines were filtered to the target plate only.**
3. **Print the number of earthquakes inside the plate.**  
   If zero, you probably used the wrong attribute, wrong CRS, or wrong predicate.
4. **Print min/max distances and inspect the top few rows.**
5. **Verify the winning row's geometry is still inside the plate in EPSG:4326.**
6. **Open the final JSON and check field names and types.**

Useful diagnostic code:

```python
def debug_ranked_results(quakes_ranked, n=5):
    cols = [c for c in ["id", "place", "time", "magnitude", "latitude", "longitude", "distance_km"] if c in quakes_ranked.columns]
    print(quakes_ranked[cols].sort_values("distance_km", ascending=False).head(n).to_string(index=False))

# Example:
# print("In-plate count:", len(quakes_in_plate))
# print("Distance CRS:", distance_crs)
# debug_ranked_results(quakes_ranked, n=10)
```

---

## Common Pitfalls

### 1. Computing distance in EPSG:4326
**Problem:** `.distance()` on lon/lat geometries returns degree-based planar distance, which is invalid for kilometer output.  
**Fix:** Reproject to a suitable projected CRS before calling `.distance()`.

### 2. Using a world-centered projection for Pacific-crossing geometries
**Problem:** Antimeridian splitting can distort distances or produce validator mismatches.  
**Fix:** For Pacific tasks, use a Pacific-centered CRS such as:
`+proj=eqc +lat_0=0 +lon_0=180 +datum=WGS84 +units=m +no_defs`

### 3. Measuring to the plate polygon edge instead of tectonic boundary lines
**Problem:** The task may provide explicit plate boundary data, and validators may expect distance to those lines, not polygon-derived boundaries.  
**Fix:** Use the boundary file filtered to the target plate whenever available.

### 4. Filtering the wrong plate due to guessing the attribute name
**Problem:** Plate code may be stored under `Code`, not `Name`, or vice versa.  
**Fix:** Inspect schema first and search candidate columns robustly.

### 5. Forgetting to dissolve/union multiple plate or boundary features
**Problem:** Measuring against only one segment or one polygon part can produce incorrect maxima.  
**Fix:** Union matching geometries before distance calculations.

### 6. Mishandling the time field
**Problem:** Raw feed time may be epoch milliseconds, timezone-naive text, or already a timestamp.  
**Fix:** Normalize with `pd.to_datetime(..., utc=True)` and format with `%Y-%m-%dT%H:%M:%SZ`.

### 7. Writing NumPy/pandas scalar types directly to JSON
**Problem:** Some validators or `json.dump` calls can choke on non-native scalar types.  
**Fix:** Explicitly cast to `str` / `float` before writing.

### 8. Including earthquakes that only touch the plate boundary
**Problem:** If you use `intersects`, boundary-touching events can slip in when the task means interior membership.  
**Fix:** Default to `predicate="within"` unless the task says otherwise.

### 9. Assuming the earthquake file already has latitude/longitude columns
**Problem:** GeoJSON earthquake feeds often store coordinates only in geometry.  
**Fix:** Derive `latitude` and `longitude` from `geometry.y` and `geometry.x` if missing.

### 10. Skipping the final file-level sanity check
**Problem:** The analysis may be correct, but the output can still fail for missing fields, wrong names, or bad timestamp formatting.  
**Fix:** Read back `/root/answer.json` and validate structure before finishing.

---

## Adaptation Notes for Similar Tasks

You can reuse this workflow for variations such as:
- âclosest earthquake to the plate boundaryâ
- âfurthest earthquake inside the Nazca plateâ
- âlargest in-plate volcanic event distance from subduction boundaryâ
- ârank all in-plate earthquakes by distance to boundaryâ

Typical changes:
- swap `idxmax()` for `idxmin()` when searching for the nearest event
- change `target_plate_code`
- adjust output fields if the schema differs
- if non-Pacific and non-dateline geometry is involved, evaluate whether another projected CRS is more suitable

---

## Minimal Execution Checklist

- [ ] Inspected all three input files
- [ ] Confirmed target plate code column
- [ ] Isolated target plate polygon
- [ ] Filtered target plate boundary lines
- [ ] Loaded earthquakes with valid point geometry
- [ ] Filtered earthquakes within the plate
- [ ] Reprojected to a suitable planar CRS
- [ ] Computed point-to-boundary distances in km
- [ ] Selected correct extreme row
- [ ] Wrote exact JSON schema
- [ ] Re-opened and validated output file

This is the safest path for tectonic plate / earthquake distance tasks that require GeoPandas-based projected analysis.