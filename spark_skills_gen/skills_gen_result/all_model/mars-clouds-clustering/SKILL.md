---
id: mars-clouds-clustering
title: Pareto Frontier Optimization for Spatial Clustering with Custom Distance Metrics
version: 1.0.0
tags: [clustering, dbscan, pareto, optimization, grid-search, citizen-science, spatial]
---

# Pareto Frontier Optimization for Spatial Clustering

## Module 1: Problem Setup and Data Understanding

### Goal
Find Pareto-optimal DBSCAN hyperparameters that balance two competing objectives (e.g., F1 score vs. centroid deviation) when clustering spatial annotations against a ground-truth reference set.

### Data Preparation Pattern
```python
import pandas as pd
import numpy as np

# Load annotation datasets
citsci = pd.read_csv('citsci_train.csv')   # columns: image_id, x, y
expert  = pd.read_csv('expert_train.csv')  # columns: image_id, x, y

# Always iterate over expert image IDs — not citizen science IDs
# Images with no citizen science points still count (F1 = 0 for those)
all_images = expert['file_rad'].unique()
```

### Key Averaging Rules
- F1 = 0.0 for images with no citsci points, no clusters, or no matches → **always included** in F1 average
- delta = NaN for those same images → **excluded** from delta average (only average over matched images)

---

## Module 2: Efficient Grid Search with Custom DBSCAN Distance

### Coordinate Transform Trick (Critical for Performance)
The custom metric `d(a,b) = sqrt((w·Δx)² + ((2-w)·Δy)²)` is equivalent to standard Euclidean distance on transformed coordinates `(w·x, (2-w)·y)`. This lets DBSCAN use `algorithm='ball_tree'` instead of a slow Python callable.

```python
from sklearn.cluster import DBSCAN
from joblib import Parallel, delayed

def cluster_and_evaluate(params, citsci_df, expert_df, all_images, max_match_dist=100):
    min_samples, epsilon, shape_weight = params
    w = shape_weight
    
    f1_scores, deltas = [], []
    
    for img_id in all_images:
        cit = citsci_df[citsci_df['file_rad'] == img_id][['x', 'y']].values
        exp = expert_df[expert_df['file_rad'] == img_id][['x', 'y']].values
        
        if len(cit) == 0:
            f1_scores.append(0.0)
            continue
        
        # Apply coordinate transform — avoids slow Python distance callable
        cit_transformed = cit * np.array([w, 2 - w])
        
        labels = DBSCAN(
            eps=epsilon, min_samples=min_samples,
            algorithm='ball_tree', metric='euclidean'
        ).fit_predict(cit_transformed)
        
        # Compute centroids (exclude noise label -1)
        unique_labels = set(labels) - {-1}
        if not unique_labels:
            f1_scores.append(0.0)
            continue
        
        centroids = np.array([cit[labels == lbl].mean(axis=0) for lbl in unique_labels])
        
        # Greedy matching: closest pairs first, standard Euclidean, max distance cap
        f1, delta = greedy_match_and_score(centroids, exp, max_dist=max_match_dist)
        f1_scores.append(f1)
        if not np.isnan(delta):
            deltas.append(delta)
    
    avg_f1   = np.mean(f1_scores)
    avg_delta = np.mean(deltas) if deltas else np.nan
    return avg_f1, avg_delta, min_samples, epsilon, round(w, 1)


def greedy_match_and_score(centroids, expert_pts, max_dist=100):
    """Greedy nearest-neighbor matching; returns (F1, mean_delta)."""
    from scipy.spatial.distance import cdist
    
    if len(expert_pts) == 0 and len(centroids) == 0:
        return 1.0, np.nan
    if len(expert_pts) == 0 or len(centroids) == 0:
        return 0.0, np.nan
    
    dist_matrix = cdist(centroids, expert_pts, metric='euclidean')
    matched_c, matched_e = set(), set()
    match_dists = []
    
    # Sort all pairs by distance, greedily assign
    pairs = sorted(
        [(dist_matrix[i, j], i, j)
         for i in range(len(centroids))
         for j in range(len(expert_pts))],
        key=lambda x: x[0]
    )
    for dist, i, j in pairs:
        if dist > max_dist:
            break
        if i not in matched_c and j not in matched_e:
            matched_c.add(i)
            matched_e.add(j)
            match_dists.append(dist)
    
    tp = len(match_dists)
    fp = len(centroids) - tp
    fn = len(expert_pts) - tp
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) > 0 else 0.0)
    delta = np.mean(match_dists) if match_dists else np.nan
    return f1, delta
```

### Parallelized Grid Search
```python
from itertools import product

min_samples_range = range(3, 10)          # 3–9
epsilon_range     = range(4, 26, 2)       # 4, 6, ..., 24
shape_weight_range = [round(w * 0.1, 1) for w in range(9, 20)]  # 0.9–1.9

all_params = list(product(min_samples_range, epsilon_range, shape_weight_range))

results = Parallel(n_jobs=-1)(
    delayed(cluster_and_evaluate)(p, citsci, expert, all_images)
    for p in all_params
)

df = pd.DataFrame(results, columns=['F1', 'delta', 'min_samples', 'epsilon', 'shape_weight'])
df = df[df['F1'] > 0.5].dropna(subset=['delta'])  # filter meaningful results
```

---

## Module 3: Pareto Frontier Extraction and Output

```python
from paretoset import paretoset

# Pareto: maximize F1, minimize delta
# paretoset expects columns where "sense" matches direction
sense = ["max", "min"]  # F1 maximized, delta minimized
mask = paretoset(df[['F1', 'delta']].values, sense=sense)
pareto_df = df[mask].copy()

# Round per spec
pareto_df['F1']           = pareto_df['F1'].round(5)
pareto_df['delta']        = pareto_df['delta'].round(5)
pareto_df['shape_weight'] = pareto_df['shape_weight'].round(1)
pareto_df['min_samples']  = pareto_df['min_samples'].astype(int)
pareto_df['epsilon']      = pareto_df['epsilon'].astype(int)

pareto_df[['F1', 'delta', 'min_samples', 'epsilon', 'shape_weight']].to_csv(
    'pareto_frontier.csv', index=False
)
```

---

## Common Pitfalls

1. **Wrong image iteration base**: Iterating over citizen science images instead of expert images causes missing F1=0 contributions for images with no citsci data, inflating average F1.

2. **Skipping the coordinate transform**: Passing a Python lambda as `metric=` to DBSCAN forces `algorithm='brute'` and is 10–50× slower. Always transform coordinates and use `metric='euclidean'`.

3. **Using custom distance for matching**: Greedy centroid-to-expert matching and delta computation must use standard Euclidean distance, not the shape-weighted one.

4. **Including NaN deltas in average**: `np.nanmean` vs `np.mean` matters — only average delta over images where at least one match was found.

5. **Forgetting F1=0 in average**: Images with no clusters or no matches must contribute F1=0 to the mean, not be skipped.

6. **Noise label in centroids**: DBSCAN labels noise points as `-1`. Always exclude label `-1` when computing cluster centroids.

7. **paretoset sense direction**: Confirm the `sense` list matches your objective directions (`"max"` for F1, `"min"` for delta). Swapping them silently produces a wrong frontier.

8. **Rounding shape_weight in search**: Floating-point accumulation (`0.9 + 0.1 + 0.1 ...`) can produce values like `1.0000000000000002`. Use `round(w, 1)` when building the parameter grid and in output.
