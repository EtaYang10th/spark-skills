---
title: Mars Cloud Clustering — DBSCAN Hyperparameter Optimization with Pareto Frontier
category: mars-clouds-clustering
domain: scientific-computing
tags:
  - DBSCAN
  - clustering
  - hyperparameter-optimization
  - pareto-frontier
  - greedy-matching
  - F1-score
  - parallel-grid-search
  - citizen-science
dependencies:
  - numpy==1.24.3
  - scipy==1.10.1
  - pandas==2.2.2
  - scikit-learn==1.2.2
  - joblib==1.3.2
---

# DBSCAN Hyperparameter Optimization for Annotation Clustering with Pareto Frontier

## Overview

This skill covers the end-to-end workflow for optimizing DBSCAN clustering of noisy citizen-science annotations against expert ground truth. The goal is to find the **Pareto frontier** of hyperparameter configurations that trade off between two objectives:

- **Maximize F1 score** — how well clusters match expert labels
- **Minimize delta** — average Euclidean distance between matched cluster centroids and expert points

The pattern generalizes to any task where you run DBSCAN per-image (or per-group), match predicted centroids to ground-truth points via greedy assignment, compute per-group metrics, aggregate, filter, and extract Pareto-optimal configurations.

---

## High-Level Workflow

1. **Load and index data** — Read citizen-science and expert CSVs. Group both by image identifier (`file_rad`). Build a lookup dict for citizen-science points keyed by image.
2. **Define the custom distance metric** — DBSCAN uses a weighted Euclidean metric controlled by `shape_weight` (w): `d(a,b) = sqrt((w*Δx)² + ((2-w)*Δy)²)`. Precompute the full pairwise distance matrix for each image+weight combo.
3. **Run DBSCAN per image** — For each image, compute the precomputed distance matrix, run DBSCAN with `metric='precomputed'`, extract cluster centroids (mean of member points).
4. **Greedy-match centroids to expert points** — Compute standard Euclidean distances between all centroid–expert pairs. Greedily assign closest pairs first (threshold ≤ 100 px). Count TP, FP, FN to get F1.
5. **Aggregate metrics across images** — Average F1 over ALL expert images (images with no citizen data or no matches get F1=0). Average delta only over images that had at least one match (skip NaN deltas).
6. **Filter and extract Pareto frontier** — Keep configs with F1 > 0.5. From those, find all Pareto-optimal points (no other point has both higher F1 and lower delta).
7. **Write output CSV** — Columns: `F1,delta,min_samples,epsilon,shape_weight` with specified rounding.

---

## Step 1: Load and Index Data

```python
import pandas as pd
import numpy as np
from collections import defaultdict

# Load datasets
citsci = pd.read_csv('/root/data/citsci_train.csv')
expert = pd.read_csv('/root/data/expert_train.csv')

# Group citizen science points by image
citsci_groups = {}
for file_rad, group in citsci.groupby('file_rad'):
    citsci_groups[file_rad] = group[['x', 'y']].values

# Group expert points by image — this defines the full set of images to evaluate
expert_groups = {}
for file_rad, group in expert.groupby('file_rad'):
    expert_groups[file_rad] = group[['x', 'y']].values

all_images = list(expert_groups.keys())
```

**Critical:** The loop must iterate over ALL images in the expert dataset. If an image has no citizen-science annotations, it contributes F1=0 and delta=NaN.

---

## Step 2: Custom Weighted Distance Matrix

```python
def compute_distance_matrix(points, shape_weight):
    """Compute pairwise custom weighted Euclidean distance matrix.
    
    d(a,b) = sqrt((w * dx)^2 + ((2-w) * dy)^2)
    
    When w=1.0, this is standard Euclidean distance.
    When w>1.0, x-distances are amplified and y-distances attenuated.
    """
    w = shape_weight
    # points is (N, 2) with columns [x, y]
    dx = points[:, 0:1] - points[:, 0:1].T  # (N, N)
    dy = points[:, 1:2] - points[:, 1:2].T  # (N, N)
    dist = np.sqrt((w * dx) ** 2 + ((2 - w) * dy) ** 2)
    return dist
```

**Why precomputed?** `sklearn.DBSCAN` supports `metric='precomputed'` which accepts a full distance matrix. This is the cleanest way to inject a custom metric without writing a Cython extension or using `metric=callable` (which is much slower for repeated calls).

---

## Step 3: Run DBSCAN and Extract Centroids

```python
from sklearn.cluster import DBSCAN

def cluster_image(points, epsilon, min_samples, shape_weight):
    """Run DBSCAN on a single image's citizen-science points.
    
    Returns array of cluster centroids (N_clusters, 2) or empty array.
    """
    if len(points) < min_samples:
        return np.empty((0, 2))
    
    dist_matrix = compute_distance_matrix(points, shape_weight)
    db = DBSCAN(eps=epsilon, min_samples=min_samples, metric='precomputed')
    labels = db.fit_predict(dist_matrix)
    
    # Extract centroids for each cluster (ignore noise label -1)
    unique_labels = set(labels)
    unique_labels.discard(-1)
    
    if not unique_labels:
        return np.empty((0, 2))
    
    centroids = []
    for label in unique_labels:
        mask = labels == label
        centroid = points[mask].mean(axis=0)
        centroids.append(centroid)
    
    return np.array(centroids)
```

---

## Step 4: Greedy Matching and F1 Computation

```python
from scipy.spatial.distance import cdist

def match_and_score(centroids, expert_points, max_dist=100.0):
    """Greedy-match centroids to expert points. Return (f1, avg_delta).
    
    Uses STANDARD Euclidean distance for matching (not the custom metric).
    Greedy: pick the globally closest unmatched pair first, repeat.
    
    Returns:
        f1: float
        delta: float or np.nan if no matches
    """
    n_centroids = len(centroids)
    n_experts = len(expert_points)
    
    if n_centroids == 0 and n_experts == 0:
        return 1.0, np.nan  # edge case: no annotations, no experts
    if n_centroids == 0 or n_experts == 0:
        return 0.0, np.nan
    
    # Standard Euclidean distance matrix
    dists = cdist(centroids, expert_points, metric='euclidean')
    
    matched_centroids = set()
    matched_experts = set()
    match_distances = []
    
    # Flatten and sort all pairs by distance
    pairs = []
    for i in range(n_centroids):
        for j in range(n_experts):
            if dists[i, j] <= max_dist:
                pairs.append((dists[i, j], i, j))
    pairs.sort()  # ascending by distance
    
    for dist_val, ci, ej in pairs:
        if ci in matched_centroids or ej in matched_experts:
            continue
        matched_centroids.add(ci)
        matched_experts.add(ej)
        match_distances.append(dist_val)
    
    tp = len(match_distances)
    fp = n_centroids - tp
    fn = n_experts - tp
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    delta = np.mean(match_distances) if match_distances else np.nan
    
    return f1, delta
```

**Critical detail:** The greedy matching sorts ALL valid pairs globally by distance, then assigns greedily. This is NOT a per-centroid nearest-neighbor — it's a global greedy assignment. This distinction matters for correctness.

---

## Step 5: Evaluate One Hyperparameter Combination

```python
def evaluate_params(params, citsci_groups, expert_groups, all_images):
    """Evaluate a single (min_samples, epsilon, shape_weight) combination.
    
    Returns dict with F1, delta, and the hyperparameters, or None if F1 <= 0.5.
    """
    min_samples, epsilon, shape_weight = params
    
    f1_scores = []
    deltas = []
    
    for image in all_images:
        expert_pts = expert_groups[image]
        citsci_pts = citsci_groups.get(image)
        
        if citsci_pts is None or len(citsci_pts) == 0:
            f1_scores.append(0.0)
            deltas.append(np.nan)
            continue
        
        centroids = cluster_image(citsci_pts, epsilon, min_samples, shape_weight)
        f1, delta = match_and_score(centroids, expert_pts)
        
        f1_scores.append(f1)
        deltas.append(delta)
    
    avg_f1 = np.mean(f1_scores)
    # Only average delta over images where matches were found (non-NaN)
    valid_deltas = [d for d in deltas if not np.isnan(d)]
    avg_delta = np.mean(valid_deltas) if valid_deltas else np.nan
    
    if avg_f1 <= 0.5:
        return None
    
    return {
        'F1': round(avg_f1, 5),
        'delta': round(avg_delta, 5),
        'min_samples': min_samples,
        'epsilon': epsilon,
        'shape_weight': round(shape_weight, 1)
    }
```

**Critical averaging rules:**
- F1: average over ALL expert images, including 0.0 for images with no citizen data or no matches.
- Delta: average only over images where at least one match was found (exclude NaN).

---

## Step 6: Parallel Grid Search

```python
from multiprocessing import Pool
from itertools import product
from functools import partial

def run_grid_search():
    # Define search space
    min_samples_range = list(range(3, 10))        # 3..9
    epsilon_range = list(range(4, 25, 2))          # 4,6,8,...,24
    shape_weight_range = [round(0.9 + i * 0.1, 1) for i in range(11)]  # 0.9..1.9
    
    all_combos = list(product(min_samples_range, epsilon_range, shape_weight_range))
    print(f"Total combinations: {len(all_combos)}")  # Should be 7 * 11 * 11 = 847
    
    # Use multiprocessing for parallelism
    eval_fn = partial(evaluate_params,
                      citsci_groups=citsci_groups,
                      expert_groups=expert_groups,
                      all_images=all_images)
    
    with Pool() as pool:
        results = pool.map(eval_fn, all_combos)
    
    # Filter out None results (F1 <= 0.5)
    results = [r for r in results if r is not None]
    print(f"Combinations with F1 > 0.5: {len(results)}")
    return results
```

**Performance note:** With ~370 images and 847 combos, this is CPU-intensive. Using `multiprocessing.Pool()` with default worker count (= number of CPUs) provides significant speedup. On a 64-core machine, the full search completes in a few minutes.

**Why `multiprocessing` over `joblib`?** Both work. `multiprocessing.Pool.map` is simpler and avoids serialization issues with `joblib` when closures capture large data. The `partial` + `Pool.map` pattern is clean and reliable.

---

## Step 7: Pareto Frontier Extraction

```python
def extract_pareto_frontier(results):
    """Find Pareto-optimal points: maximize F1, minimize delta.
    
    A point is Pareto-optimal if no other point has BOTH higher F1 AND lower delta.
    """
    if not results:
        return []
    
    pareto = []
    for i, ri in enumerate(results):
        dominated = False
        for j, rj in enumerate(results):
            if i == j:
                continue
            # rj dominates ri if rj has >= F1 and <= delta, with at least one strict
            if (rj['F1'] >= ri['F1'] and rj['delta'] <= ri['delta'] and
                (rj['F1'] > ri['F1'] or rj['delta'] < ri['delta'])):
                dominated = True
                break
        if not dominated:
            pareto.append(ri)
    
    return pareto
```

**Alternative:** You can use the `paretoset` library if installed:

```python
from paretoset import paretoset
import pandas as pd

df = pd.DataFrame(results)
# For paretoset: specify sense per objective
mask = paretoset(df[['F1', 'delta']], sense=['max', 'min'])
pareto_df = df[mask]
```

Both approaches produce the same result. The manual O(n²) loop is fine for ~500 points.

---

## Step 8: Write Output

```python
def write_output(pareto_results, output_path='/root/pareto_frontier.csv'):
    df = pd.DataFrame(pareto_results)
    df = df[['F1', 'delta', 'min_samples', 'epsilon', 'shape_weight']]
    df = df.sort_values('F1', ascending=False).reset_index(drop=True)
    df.to_csv(output_path, index=False)
    print(f"Wrote {len(df)} Pareto-optimal points to {output_path}")
    return df
```

**Rounding is done during `evaluate_params`:** F1 and delta to 5 decimal places, shape_weight to 1 decimal place. `min_samples` and `epsilon` are already integers.

---

## Common Pitfalls

### 1. Averaging F1 over wrong image set
**Wrong:** Only average F1 over images that have citizen-science annotations.
**Right:** Average F1 over ALL images in the expert dataset. Images with no citizen data get F1=0.0, which pulls the average down. This is intentional — a clustering that misses entire images should be penalized.

### 2. Averaging delta over NaN values
**Wrong:** Include NaN deltas in the average (produces NaN overall) or replace NaN with 0.
**Right:** Exclude NaN deltas from the average. Only images with at least one matched centroid–expert pair contribute to the delta average.

### 3. Using custom metric for matching
**Wrong:** Use the weighted distance metric for greedy matching of centroids to expert points.
**Right:** Use **standard Euclidean distance** for matching. The custom metric is only for DBSCAN clustering. The matching and delta computation use standard Euclidean.

### 4. Per-centroid nearest-neighbor instead of global greedy
**Wrong:** For each centroid, find its nearest expert and match.
**Right:** Compute ALL pairwise distances, sort globally, and greedily assign the closest unmatched pair. This avoids conflicts where two centroids claim the same expert.

### 5. Forgetting the max distance threshold
**Wrong:** Match any centroid to any expert regardless of distance.
**Right:** Only pairs within 100 pixels (standard Euclidean) are eligible for matching.

### 6. DBSCAN with too few points
If an image has fewer points than `min_samples`, DBSCAN labels everything as noise. Handle this gracefully — return empty centroids, which yields F1=0.

### 7. Floating-point shape_weight values
When generating the grid, use `round(0.9 + i * 0.1, 1)` to avoid floating-point drift (e.g., 1.0000000000000002). Always round shape_weight to 1 decimal place in output.

### 8. Pareto dominance with equal values
Point A dominates point B only if A is **at least as good** on both objectives AND **strictly better** on at least one. Two points with identical (F1, delta) do NOT dominate each other — both belong on the frontier.

---

## Verification Checklist

Before finalizing, verify:

1. **Output file exists** at the expected path and is non-empty.
2. **Column order** matches exactly: `F1,delta,min_samples,epsilon,shape_weight`.
3. **All F1 values > 0.5** in the output (the filter was applied).
4. **All delta values > 0** (distances should be positive).
5. **Hyperparameters in valid ranges:** min_samples 3–9, epsilon 4–24 (even), shape_weight 0.9–1.9.
6. **No dominated points:** For every pair (i, j) in the output, it must NOT be the case that one dominates the other.
7. **Rounding:** F1 and delta to 5 decimal places, shape_weight to 1 decimal place.

```python
# Quick validation script
df = pd.read_csv('/root/pareto_frontier.csv')
assert len(df) > 0, "Empty output"
assert list(df.columns) == ['F1', 'delta', 'min_samples', 'epsilon', 'shape_weight']
assert (df.F1 > 0.5).all(), "F1 values must be > 0.5"
assert (df.delta > 0).all(), "Delta values must be positive"
assert df.min_samples.between(3, 9).all()
assert df.epsilon.between(4, 24).all()
assert df.shape_weight.between(0.9, 1.9).all()

# Verify Pareto optimality
vals = df[['F1', 'delta']].values
for i in range(len(vals)):
    for j in range(len(vals)):
        if i == j:
            continue
        assert not (vals[j][0] >= vals[i][0] and vals[j][1] <= vals[i][1] and
                    (vals[j][0] > vals[i][0] or vals[j][1] < vals[i][1])), \
            f"Point {i} is dominated by point {j}"
print(f"VALID: {len(df)} Pareto-optimal points")
```

---

## Reference Implementation

This is the complete, self-contained script. Copy, adapt paths if needed, and run.

```python
#!/usr/bin/env python3
"""
DBSCAN hyperparameter optimization for Mars cloud annotation clustering.
Finds the Pareto frontier of (F1, delta) across a grid of
(min_samples, epsilon, shape_weight) combinations.

Usage:
    python3 solve.py

Output:
    /root/pareto_frontier.csv
"""

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from scipy.spatial.distance import cdist
from multiprocessing import Pool
from itertools import product
from functools import partial

# ---------------------------------------------------------------------------
# 1. Data loading
# ---------------------------------------------------------------------------

def load_data(citsci_path, expert_path):
    citsci = pd.read_csv(citsci_path)
    expert = pd.read_csv(expert_path)

    citsci_groups = {}
    for file_rad, group in citsci.groupby('file_rad'):
        citsci_groups[file_rad] = group[['x', 'y']].values

    expert_groups = {}
    for file_rad, group in expert.groupby('file_rad'):
        expert_groups[file_rad] = group[['x', 'y']].values

    all_images = sorted(expert_groups.keys())
    return citsci_groups, expert_groups, all_images

# ---------------------------------------------------------------------------
# 2. Custom distance matrix for DBSCAN
# ---------------------------------------------------------------------------

def compute_distance_matrix(points, shape_weight):
    """Weighted Euclidean: d = sqrt((w*dx)^2 + ((2-w)*dy)^2)"""
    w = shape_weight
    w2 = 2.0 - w
    dx = points[:, 0:1] - points[:, 0:1].T
    dy = points[:, 1:2] - points[:, 1:2].T
    return np.sqrt((w * dx) ** 2 + (w2 * dy) ** 2)

# ---------------------------------------------------------------------------
# 3. DBSCAN clustering → centroids
# ---------------------------------------------------------------------------

def cluster_image(points, epsilon, min_samples, shape_weight):
    if len(points) < min_samples:
        return np.empty((0, 2))

    dist_matrix = compute_distance_matrix(points, shape_weight)
    labels = DBSCAN(eps=epsilon, min_samples=min_samples,
                    metric='precomputed').fit_predict(dist_matrix)

    unique_labels = set(labels)
    unique_labels.discard(-1)
    if not unique_labels:
        return np.empty((0, 2))

    centroids = []
    for lab in unique_labels:
        centroids.append(points[labels == lab].mean(axis=0))
    return np.array(centroids)

# ---------------------------------------------------------------------------
# 4. Greedy matching + F1 / delta
# ---------------------------------------------------------------------------

def match_and_score(centroids, expert_points, max_dist=100.0):
    n_c = len(centroids)
    n_e = len(expert_points)

    if n_c == 0 and n_e == 0:
        return 1.0, np.nan
    if n_c == 0 or n_e == 0:
        return 0.0, np.nan

    # Standard Euclidean for matching (NOT the custom metric)
    dists = cdist(centroids, expert_points, metric='euclidean')

    # Build sorted list of eligible pairs
    pairs = []
    for i in range(n_c):
        for j in range(n_e):
            if dists[i, j] <= max_dist:
                pairs.append((dists[i, j], i, j))
    pairs.sort()

    matched_c = set()
    matched_e = set()
    match_dists = []

    for d, ci, ej in pairs:
        if ci in matched_c or ej in matched_e:
            continue
        matched_c.add(ci)
        matched_e.add(ej)
        match_dists.append(d)

    tp = len(match_dists)
    fp = n_c - tp
    fn = n_e - tp

    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    delta = float(np.mean(match_dists)) if match_dists else np.nan

    return f1, delta

# ---------------------------------------------------------------------------
# 5. Evaluate one hyperparameter combination
# ---------------------------------------------------------------------------

def evaluate_params(params, citsci_groups, expert_groups, all_images):
    min_samples, epsilon, shape_weight = params

    f1_scores = []
    deltas = []

    for image in all_images:
        expert_pts = expert_groups[image]
        citsci_pts = citsci_groups.get(image)

        if citsci_pts is None or len(citsci_pts) == 0:
            f1_scores.append(0.0)
            deltas.append(np.nan)
            continue

        centroids = cluster_image(citsci_pts, epsilon, min_samples, shape_weight)
        f1, delta = match_and_score(centroids, expert_pts)
        f1_scores.append(f1)
        deltas.append(delta)

    avg_f1 = float(np.mean(f1_scores))
    valid_deltas = [d for d in deltas if not np.isnan(d)]
    avg_delta = float(np.mean(valid_deltas)) if valid_deltas else np.nan

    if avg_f1 <= 0.5:
        return None

    return {
        'F1': round(avg_f1, 5),
        'delta': round(avg_delta, 5),
        'min_samples': min_samples,
        'epsilon': epsilon,
        'shape_weight': round(shape_weight, 1),
    }

# ---------------------------------------------------------------------------
# 6. Pareto frontier extraction
# ---------------------------------------------------------------------------

def extract_pareto_frontier(results):
    """Maximize F1, minimize delta. O(n^2) dominance check."""
    pareto = []
    for i, ri in enumerate(results):
        dominated = False
        for j, rj in enumerate(results):
            if i == j:
                continue
            if (rj['F1'] >= ri['F1'] and rj['delta'] <= ri['delta'] and
                    (rj['F1'] > ri['F1'] or rj['delta'] < ri['delta'])):
                dominated = True
                break
        if not dominated:
            pareto.append(ri)
    return pareto

# ---------------------------------------------------------------------------
# 7. Main
# ---------------------------------------------------------------------------

def main():
    citsci_path = '/root/data/citsci_train.csv'
    expert_path = '/root/data/expert_train.csv'
    output_path = '/root/pareto_frontier.csv'

    print("Loading data...")
    citsci_groups, expert_groups, all_images = load_data(citsci_path, expert_path)
    print(f"  {len(all_images)} expert images, "
          f"{len(citsci_groups)} images with citizen-science annotations")

    # Search space
    min_samples_range  = list(range(3, 10))                                  # 3..9
    epsilon_range      = list(range(4, 25, 2))                               # 4,6,...,24
    shape_weight_range = [round(0.9 + i * 0.1, 1) for i in range(11)]       # 0.9..1.9

    all_combos = list(product(min_samples_range, epsilon_range, shape_weight_range))
    print(f"Grid search: {len(all_combos)} combinations "
          f"({len(min_samples_range)}×{len(epsilon_range)}×{len(shape_weight_range)})")

    # Parallel evaluation
    eval_fn = partial(evaluate_params,
                      citsci_groups=citsci_groups,
                      expert_groups=expert_groups,
                      all_images=all_images)

    print("Running parallel grid search...")
    with Pool() as pool:
        raw_results = pool.map(eval_fn, all_combos)

    results = [r for r in raw_results if r is not None]
    print(f"  {len(results)} combinations with F1 > 0.5")

    # Pareto frontier
    pareto = extract_pareto_frontier(results)
    print(f"  {len(pareto)} Pareto-optimal points")

    # Write output
    df = pd.DataFrame(pareto)
    df = df[['F1', 'delta', 'min_samples', 'epsilon', 'shape_weight']]
    df = df.sort_values('F1', ascending=False).reset_index(drop=True)
    df.to_csv(output_path, index=False)
    print(f"Wrote {output_path}")

    # Quick sanity check
    print(f"\n  F1 range:    [{df.F1.min()}, {df.F1.max()}]")
    print(f"  Delta range: [{df.delta.min()}, {df.delta.max()}]")

if __name__ == '__main__':
    main()
```

---

## Domain Notes

- **shape_weight > 1** attenuates y-distances, making DBSCAN more tolerant of vertical spread. In practice, the Pareto-optimal solutions tend to cluster around high shape_weight values (1.6–1.9), suggesting Mars cloud annotations have more vertical scatter than horizontal.
- **epsilon 8–20** is the typical sweet spot. Very small epsilon fragments clusters; very large epsilon merges distinct clouds.
- The task is embarrassingly parallel across hyperparameter combinations. Each combo independently loops over all images. Use all available CPU cores.
- With ~370 images and 847 combos, expect ~5–15 minutes on a multi-core machine. Single-threaded would take hours.
- The `paretoset` library is available in the environment and can replace the manual Pareto extraction, but the manual O(n²) check is simple and sufficient for ~500 candidate points.