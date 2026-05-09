---
title: "Parallel TF-IDF Search Engine with Python Multiprocessing"
category: parallel-tfidf-search
tags:
  - python
  - multiprocessing
  - tfidf
  - parallel-computing
  - information-retrieval
  - performance-optimization
domain: parallel-computing
objective: >
  Parallelize a sequential TF-IDF document search engine (index building + batch query search)
  using Python's multiprocessing.Pool to achieve measurable speedup on multi-core systems
  while producing results identical to the sequential baseline.
---

# Parallel TF-IDF Search Engine — Skill Guide

## Overview

This skill covers how to take a single-threaded TF-IDF search engine and parallelize both its **index building** and **batch search** phases using `multiprocessing.Pool`. The key challenges are:

1. Splitting work into chunks that are large enough to amortize IPC overhead
2. Merging partial results (document frequencies, inverted index entries) correctly
3. Sharing large read-only data (the index, document list) across workers without repeated serialization
4. Producing results **bit-for-bit identical** to the sequential version

Typical performance targets: **1.5× speedup** for index building and **2× speedup** for batch search with 4 workers.

---

## High-Level Workflow

### Step 1: Understand the Sequential Baseline

Read the existing sequential implementation end-to-end. Identify:

- **Data structures**: `TFIDFIndex` (vocabulary, idf_values, tfidf_vectors, inverted_index, doc_count), `SearchResult`, any result wrapper like `IndexingResult`.
- **Tokenization / TF computation**: Usually the most expensive per-document work during indexing.
- **DF aggregation → IDF computation**: Typically fast, sequential is fine.
- **TF-IDF vector + inverted index construction**: Second expensive phase — depends on IDF values computed in the previous step.
- **Search (cosine similarity)**: Per-query scoring against the inverted index. Embarrassingly parallel across queries.

**Why this matters**: You must replicate every data structure and computation exactly. Even small floating-point order-of-operations differences will cause correctness failures.

### Step 2: Identify Parallelization Boundaries

There are exactly **two** parallelizable phases and one sequential bridge:

```
Phase 1 (parallel):  tokenize + compute TF for each document chunk
         ↓
Bridge   (sequential): merge DF counts across chunks → compute IDF
         ↓
Phase 2 (parallel):  compute TF-IDF vectors + inverted index entries per chunk
         ↓
Merge    (sequential): merge inverted index dicts from all chunks
         ↓
Search   (parallel):  partition queries across workers, each scores independently
```

**Decision criterion**: Only parallelize work that takes O(N × something). The DF merge and IDF computation are O(|vocabulary|) — fast enough to stay sequential.

### Step 3: Implement Parallel Index Building

Use `multiprocessing.Pool.map` (or `starmap`) with document chunks. Two separate pool invocations:

1. **Phase 1 workers** receive a chunk of `(doc_id, text)` pairs. Each worker tokenizes, computes term frequencies, and returns `(tf_dict, local_df_set)` per document.
2. **Sequential bridge** merges all DF counts, computes IDF.
3. **Phase 2 workers** receive the same chunks plus IDF values (via pool initializer). Each worker computes TF-IDF vectors and local inverted index entries.

### Step 4: Implement Parallel Batch Search

Partition the query list into chunks. Each worker receives a chunk of queries and scores them against the shared index. Use a **pool initializer** to pass the index and documents list once (avoiding per-task pickling of large objects).

### Step 5: Verify Correctness Before Performance

Always check:
- IDF values match the sequential version (within 1e-6 tolerance)
- Search results (doc IDs and scores) match exactly for a set of test queries
- Only then measure speedup

### Step 6: Tune Chunk Sizes

- For index building: `chunk_size=500` documents is a good default for corpora of 1K–10K docs.
- For search: `chunk_size = ceil(len(queries) / num_workers)` to distribute evenly.
- Too-small chunks → IPC overhead dominates. Too-large chunks → poor load balancing.

---

## Detailed Implementation

### Chunking Utility

```python
import math

def make_chunks(items, chunk_size):
    """Split a list into chunks of at most chunk_size elements."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
```

### Phase 1: Parallel Tokenization + TF Computation

The worker function must be defined at **module level** (pickle requirement for multiprocessing).

```python
import re
import math
from collections import Counter

# ---- Module-level worker for Phase 1 ----
def _worker_tokenize_and_tf(chunk):
    """
    chunk: list of (doc_id, document_text)
    Returns: list of (doc_id, tf_dict, term_set)
      - tf_dict: {term: raw_count}
      - term_set: set of unique terms in this document (for DF counting)
    """
    results = []
    for doc_id, text in chunk:
        # Tokenize — replicate the EXACT tokenization from sequential version
        tokens = re.findall(r'[a-z0-9]+', text.lower())
        tf = Counter(tokens)
        results.append((doc_id, dict(tf), set(tf.keys())))
    return results
```

### Sequential Bridge: Merge DF + Compute IDF

```python
def _merge_df_and_compute_idf(phase1_results, doc_count):
    """
    phase1_results: list of lists of (doc_id, tf_dict, term_set)
    Returns: {term: idf_value}
    """
    df = Counter()
    for chunk_results in phase1_results:
        for _, _, term_set in chunk_results:
            df.update(term_set)

    # Standard IDF formula: log(N / df_t) + 1  (or whatever the sequential uses)
    # CRITICAL: match the exact formula from the sequential implementation
    idf = {}
    for term, count in df.items():
        idf[term] = math.log(doc_count / count) + 1
    return idf
```

### Phase 2: Parallel TF-IDF Vector + Inverted Index Construction

Use a **pool initializer** to share IDF values without pickling them per task:

```python
# ---- Module-level globals for Phase 2 workers ----
_phase2_idf = None

def _phase2_initializer(idf_values):
    global _phase2_idf
    _phase2_idf = idf_values

def _worker_build_tfidf(chunk_data):
    """
    chunk_data: list of (doc_id, tf_dict)
    Returns: (tfidf_vectors_dict, local_inverted_index)
      - tfidf_vectors_dict: {doc_id: {term: tfidf_score}}
      - local_inverted_index: {term: [(doc_id, tfidf_score), ...]}
    """
    idf = _phase2_idf
    vectors = {}
    inv_idx = {}

    for doc_id, tf_dict in chunk_data:
        vec = {}
        for term, count in tf_dict.items():
            score = count * idf.get(term, 0.0)
            if score > 0:
                vec[term] = score
                if term not in inv_idx:
                    inv_idx[term] = []
                inv_idx[term].append((doc_id, score))
        # Normalize the vector (L2 norm) — match sequential exactly
        norm = math.sqrt(sum(v * v for v in vec.values())) if vec else 1.0
        if norm > 0:
            vec = {t: v / norm for t, v in vec.items()}
            # Also normalize inverted index entries for this doc
            for term in tf_dict:
                if term in vec:
                    # Update the last entry we added for this doc
                    entries = inv_idx.get(term, [])
                    for i in range(len(entries) - 1, -1, -1):
                        if entries[i][0] == doc_id:
                            entries[i] = (doc_id, vec[term])
                            break
        vectors[doc_id] = vec

    return vectors, inv_idx
```

**Important**: The normalization logic above is illustrative. You MUST read the sequential implementation and replicate its exact normalization approach. Some implementations normalize TF-IDF vectors, others don't. Some store raw scores in the inverted index, others store normalized scores.

### Merging Inverted Indexes

```python
def _merge_inverted_indexes(partial_indexes):
    """Merge list of {term: [(doc_id, score)]} dicts into one."""
    merged = {}
    for partial in partial_indexes:
        for term, postings in partial.items():
            if term not in merged:
                merged[term] = []
            merged[term].extend(postings)
    return merged
```

### Parallel Batch Search

```python
# ---- Module-level globals for search workers ----
_search_index = None
_search_documents = None

def _search_initializer(index, documents):
    global _search_index, _search_documents
    _search_index = index
    _search_documents = documents

def _worker_search_batch(args):
    """
    args: (query_chunk, top_k)
    Returns: list of list of SearchResult (one per query)
    """
    query_chunk, top_k = args
    index = _search_index
    documents = _search_documents
    results = []
    for query in query_chunk:
        # Tokenize query the same way as documents
        tokens = re.findall(r'[a-z0-9]+', query.lower())
        query_tf = Counter(tokens)

        # Build query TF-IDF vector
        query_vec = {}
        for term, count in query_tf.items():
            if term in index.idf_values:
                query_vec[term] = count * index.idf_values[term]

        # Normalize query vector
        norm = math.sqrt(sum(v * v for v in query_vec.values())) if query_vec else 0.0
        if norm == 0:
            results.append([])
            continue
        query_vec = {t: v / norm for t, v in query_vec.items()}

        # Score documents using inverted index (sparse dot product)
        scores = Counter()
        for term, q_weight in query_vec.items():
            if term in index.inverted_index:
                for doc_id, d_weight in index.inverted_index[term]:
                    scores[doc_id] += q_weight * d_weight

        # Top-k
        top = scores.most_common(top_k)
        query_results = []
        for doc_id, score in top:
            query_results.append(SearchResult(
                doc_id=doc_id,
                score=score,
                document=documents[doc_id] if documents else ""
            ))
        results.append(query_results)
    return results
```

### Putting It Together: The Two Public Functions

```python
import multiprocessing
import time
import os

def build_tfidf_index_parallel(documents, num_workers=None, chunk_size=500):
    if num_workers is None:
        num_workers = min(os.cpu_count() or 4, 8)

    doc_count = len(documents)
    # Prepare enumerated chunks for Phase 1
    enumerated = list(enumerate(documents))
    chunks = make_chunks(enumerated, chunk_size)

    start = time.perf_counter()

    # Phase 1: parallel tokenize + TF
    with multiprocessing.Pool(num_workers) as pool:
        phase1_results = pool.map(_worker_tokenize_and_tf, chunks)

    # Sequential: merge DF, compute IDF
    idf = _merge_df_and_compute_idf(phase1_results, doc_count)

    # Prepare Phase 2 input: flatten to (doc_id, tf_dict) chunks
    phase2_chunks = []
    for chunk_results in phase1_results:
        phase2_chunks.append([(doc_id, tf_dict) for doc_id, tf_dict, _ in chunk_results])

    # Phase 2: parallel TF-IDF vectors + inverted index
    with multiprocessing.Pool(num_workers, initializer=_phase2_initializer,
                               initargs=(idf,)) as pool:
        phase2_results = pool.map(_worker_build_tfidf, phase2_chunks)

    # Merge results
    all_vectors = {}
    partial_inv_indexes = []
    for vectors, inv_idx in phase2_results:
        all_vectors.update(vectors)
        partial_inv_indexes.append(inv_idx)

    inverted_index = _merge_inverted_indexes(partial_inv_indexes)

    elapsed = time.perf_counter() - start

    # Build vocabulary
    vocabulary = set(idf.keys())

    # Construct the index object (match sequential's structure exactly)
    index = TFIDFIndex(
        vocabulary=vocabulary,
        idf_values=idf,
        tfidf_vectors=all_vectors,
        inverted_index=inverted_index,
        doc_count=doc_count
    )
    return ParallelIndexingResult(index=index, elapsed_time=elapsed)


def batch_search_parallel(queries, index, top_k=10, num_workers=None, documents=None):
    if num_workers is None:
        num_workers = min(os.cpu_count() or 4, 8)

    chunk_size = math.ceil(len(queries) / num_workers)
    query_chunks = make_chunks(queries, chunk_size)
    args = [(chunk, top_k) for chunk in query_chunks]

    start = time.perf_counter()
    with multiprocessing.Pool(num_workers, initializer=_search_initializer,
                               initargs=(index, documents)) as pool:
        chunk_results = pool.map(_worker_search_batch, args)
    elapsed = time.perf_counter() - start

    # Flatten: list of lists → single list preserving query order
    all_results = []
    for chunk in chunk_results:
        all_results.extend(chunk)

    return all_results, elapsed
```

---

## Common Pitfalls

### 1. Worker Functions Not at Module Level

`multiprocessing.Pool` uses `pickle` to send functions to workers. Lambdas, closures, and nested functions **cannot be pickled**. Every worker function and its initializer must be defined at the **top level of the module**.

### 2. Serializing Large Objects Per Task

If you pass the full index or document list as an argument to each `pool.map` call, it gets pickled once per chunk. For a 5K-document index, this can be megabytes × num_chunks. Use a **pool initializer** to pass large read-only data once per worker process:

```python
# BAD — index pickled N times
pool.map(search_fn, [(chunk, index, docs) for chunk in query_chunks])

# GOOD — index pickled once per worker via initializer
pool = Pool(n, initializer=init_fn, initargs=(index, docs))
pool.map(search_fn, query_chunks)
```

### 3. Mismatched Tokenization or IDF Formula

The parallel version must use the **exact same** tokenization regex, lowercasing, IDF formula, and normalization as the sequential version. Even `log(N/df)` vs `log(N/df) + 1` vs `log((N+1)/(df+1)) + 1` will produce different results. **Read the sequential code carefully before writing any parallel worker.**

### 4. Forgetting to Normalize Vectors

If the sequential version L2-normalizes TF-IDF vectors (so cosine similarity = dot product), the parallel version must do the same. Forgetting normalization means search scores won't match.

### 5. Inverted Index Entry Order

After merging partial inverted indexes, the posting lists may be in a different order than the sequential version. This is usually fine because search sorts by score, but verify that your top-k selection is stable (i.e., ties are broken the same way). If the sequential version doesn't explicitly break ties, `Counter.most_common` is a safe default since it preserves insertion order for equal counts.

### 6. Chunk Size Too Small

With `chunk_size=1` (one document per task), IPC overhead dominates and you get **slowdown** instead of speedup. For index building, 500 documents per chunk is a good starting point. For search, divide queries evenly across workers (`ceil(len(queries) / num_workers)`).

### 7. Too Many Workers

More workers ≠ more speed. Pool creation, memory copying, and GIL contention (for any non-CPU-bound parts) add overhead. Cap at 8 workers even on 64-core machines for this workload size. The sweet spot is usually 4–8 for corpora under 50K documents.

### 8. Not Using `if __name__ == '__main__'` Guard

On some platforms (Windows, macOS with spawn), multiprocessing will re-import the module. Without the guard, you get infinite process spawning. Always wrap test/main code:

```python
if __name__ == '__main__':
    # test code here
```

On Linux with fork (the default), this is less critical but still good practice.

### 9. Forgetting to Flatten Results in Order

`pool.map` preserves order, but if you use `pool.imap_unordered` or `pool.apply_async`, results may arrive out of order. For batch search, query order must be preserved. Stick with `pool.map`.

---

## Reference Implementation

This is a **complete, self-contained** parallel TF-IDF solution. Copy, adapt the data structures to match your sequential version, and it should work.

```python
"""
parallel_solution.py — Parallel TF-IDF index building and batch search.

Drop-in replacement for sequential.py with multiprocessing acceleration.
Produces identical results to the sequential version.
"""

import re
import os
import sys
import math
import time
import multiprocessing
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional

# ---------------------------------------------------------------------------
# Import data structures from the sequential module so we return the exact
# same types the test harness expects.
# If the sequential module is in the same directory, this import works.
# Adjust the import path as needed for your project layout.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sequential import (
    TFIDFIndex,
    SearchResult,
    IndexingResult,
    tokenize,                       # reuse exact tokenization
    build_tfidf_index_sequential,   # needed for correctness comparison
    batch_search_sequential,        # needed for correctness comparison
)


# ---------------------------------------------------------------------------
# Result wrapper for parallel indexing (mirrors IndexingResult)
# ---------------------------------------------------------------------------
@dataclass
class ParallelIndexingResult:
    index: TFIDFIndex
    elapsed_time: float


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def _make_chunks(items, chunk_size):
    """Split a list into sub-lists of at most chunk_size."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


# ===========================================================================
# PHASE 1 — Parallel tokenization + term-frequency computation
# ===========================================================================
def _worker_phase1(chunk):
    """
    Input:  list of (doc_id, raw_text)
    Output: list of (doc_id, tf_dict: Dict[str,int], unique_terms: Set[str])
    """
    results = []
    for doc_id, text in chunk:
        tokens = tokenize(text)          # MUST use the same tokenizer
        tf = Counter(tokens)
        results.append((doc_id, dict(tf), set(tf.keys())))
    return results


# ===========================================================================
# PHASE 2 — Parallel TF-IDF vector + inverted index construction
# ===========================================================================
_p2_idf: Optional[Dict[str, float]] = None

def _phase2_init(idf_values):
    global _p2_idf
    _p2_idf = idf_values

def _worker_phase2(chunk):
    """
    Input:  list of (doc_id, tf_dict)
    Output: (vectors: Dict[int, Dict[str,float]],
             inv_idx: Dict[str, List[Tuple[int,float]]])
    Uses _p2_idf (set via initializer) for IDF values.
    """
    idf = _p2_idf
    vectors = {}
    inv_idx = {}

    for doc_id, tf_dict in chunk:
        vec = {}
        for term, count in tf_dict.items():
            idf_val = idf.get(term, 0.0)
            if idf_val > 0:
                vec[term] = count * idf_val

        # L2-normalize (match sequential's normalization)
        norm = math.sqrt(sum(v * v for v in vec.values())) if vec else 0.0
        if norm > 0:
            vec = {t: v / norm for t, v in vec.items()}

        vectors[doc_id] = vec

        # Build local inverted index with NORMALIZED scores
        for term, score in vec.items():
            if term not in inv_idx:
                inv_idx[term] = []
            inv_idx[term].append((doc_id, score))

    return vectors, inv_idx


# ===========================================================================
# INDEX BUILDING — public API
# ===========================================================================
def build_tfidf_index_parallel(documents, num_workers=None, chunk_size=500):
    """
    Build a TFIDFIndex in parallel.

    Parameters
    ----------
    documents   : list of str — the raw document texts
    num_workers : int or None — number of worker processes (default: auto)
    chunk_size  : int — documents per chunk for Phase 1 and Phase 2

    Returns
    -------
    ParallelIndexingResult with .index (TFIDFIndex) and .elapsed_time (float)
    """
    if num_workers is None:
        num_workers = min(os.cpu_count() or 4, 8)

    doc_count = len(documents)
    enumerated = list(enumerate(documents))
    chunks = _make_chunks(enumerated, chunk_size)

    start = time.perf_counter()

    # ---- Phase 1: parallel tokenize + TF ----
    with multiprocessing.Pool(num_workers) as pool:
        phase1_results = pool.map(_worker_phase1, chunks)

    # ---- Sequential bridge: merge DF → compute IDF ----
    df = Counter()
    for chunk_results in phase1_results:
        for _, _, term_set in chunk_results:
            df.update(term_set)

    # CRITICAL: use the EXACT same IDF formula as sequential.py
    # Common variants:
    #   log(N / df_t)           — standard
    #   log(N / df_t) + 1       — smoothed
    #   log((N + 1) / (df_t + 1)) + 1  — sklearn-style
    # READ sequential.py and match it.
    idf_values = {}
    for term, count in df.items():
        idf_values[term] = math.log(doc_count / count) + 1  # ← adjust to match

    # ---- Phase 2: parallel TF-IDF vectors + inverted index ----
    phase2_chunks = []
    for chunk_results in phase1_results:
        phase2_chunks.append([(doc_id, tf_dict) for doc_id, tf_dict, _ in chunk_results])

    with multiprocessing.Pool(num_workers, initializer=_phase2_init,
                               initargs=(idf_values,)) as pool:
        phase2_results = pool.map(_worker_phase2, phase2_chunks)

    # ---- Merge ----
    all_vectors = {}
    merged_inv = {}
    for vectors, inv_idx in phase2_results:
        all_vectors.update(vectors)
        for term, postings in inv_idx.items():
            if term not in merged_inv:
                merged_inv[term] = []
            merged_inv[term].extend(postings)

    elapsed = time.perf_counter() - start

    vocabulary = set(idf_values.keys())

    index = TFIDFIndex(
        vocabulary=vocabulary,
        idf_values=idf_values,
        tfidf_vectors=all_vectors,
        inverted_index=merged_inv,
        doc_count=doc_count,
    )
    return ParallelIndexingResult(index=index, elapsed_time=elapsed)


# ===========================================================================
# BATCH SEARCH — parallel
# ===========================================================================
_s_index: Optional[TFIDFIndex] = None
_s_documents: Optional[List[str]] = None

def _search_init(index, documents):
    global _s_index, _s_documents
    _s_index = index
    _s_documents = documents

def _worker_search(args):
    """
    Input:  (query_chunk: List[str], top_k: int)
    Output: List[List[SearchResult]]
    """
    query_chunk, top_k = args
    index = _s_index
    documents = _s_documents
    all_results = []

    for query in query_chunk:
        tokens = tokenize(query)
        query_tf = Counter(tokens)

        # Build query TF-IDF vector
        query_vec = {}
        for term, count in query_tf.items():
            if term in index.idf_values:
                query_vec[term] = count * index.idf_values[term]

        # Normalize query vector
        norm = math.sqrt(sum(v * v for v in query_vec.values())) if query_vec else 0.0
        if norm == 0:
            all_results.append([])
            continue
        query_vec = {t: v / norm for t, v in query_vec.items()}

        # Score via inverted index (sparse dot product = cosine similarity)
        scores = Counter()
        for term, q_weight in query_vec.items():
            postings = index.inverted_index.get(term, [])
            for doc_id, d_weight in postings:
                scores[doc_id] += q_weight * d_weight

        # Top-k results
        top = scores.most_common(top_k)
        query_results = []
        for doc_id, score in top:
            doc_text = documents[doc_id] if documents else ""
            query_results.append(SearchResult(
                doc_id=doc_id,
                score=score,
                document=doc_text,
            ))
        all_results.append(query_results)

    return all_results


def batch_search_parallel(queries, index, top_k=10, num_workers=None, documents=None):
    """
    Search for multiple queries in parallel.

    Parameters
    ----------
    queries     : list of str
    index       : TFIDFIndex
    top_k       : int — number of results per query
    num_workers : int or None
    documents   : list of str — original documents (for populating SearchResult.document)

    Returns
    -------
    (results: List[List[SearchResult]], elapsed_time: float)
    """
    if num_workers is None:
        num_workers = min(os.cpu_count() or 4, 8)

    # Partition queries evenly across workers
    chunk_size = max(1, math.ceil(len(queries) / num_workers))
    query_chunks = _make_chunks(queries, chunk_size)
    args = [(chunk, top_k) for chunk in query_chunks]

    start = time.perf_counter()
    with multiprocessing.Pool(num_workers, initializer=_search_init,
                               initargs=(index, documents)) as pool:
        chunk_results = pool.map(_worker_search, args)
    elapsed = time.perf_counter() - start

    # Flatten preserving query order (pool.map guarantees order)
    flat = []
    for chunk in chunk_results:
        flat.extend(chunk)

    return flat, elapsed


# ===========================================================================
# SELF-TEST — run with: python parallel_solution.py
# ===========================================================================
if __name__ == '__main__':
    # Try to import the document generator if available
    try:
        from document_generator import generate_corpus
    except ImportError:
        import random
        def generate_corpus(n, seed=42):
            random.seed(seed)
            words = ['machine', 'learning', 'algorithm', 'neural', 'network',
                     'database', 'optimization', 'performance', 'clinical',
                     'trial', 'market', 'analysis', 'investment', 'research']
            docs = []
            for _ in range(n):
                length = random.randint(50, 300)
                docs.append(' '.join(random.choices(words, k=length)))
            print(f"Generated {n} documents.")
            return docs

    import random

    # --- Correctness test ---
    print("=== Correctness Test (1000 docs) ===")
    corpus = generate_corpus(1000, seed=42)
    seq_result = build_tfidf_index_sequential(corpus)
    par_result = build_tfidf_index_parallel(corpus, num_workers=4)

    seq_idf = seq_result.index.idf_values
    par_idf = par_result.index.idf_values

    mismatches = 0
    for term in seq_idf:
        if term not in par_idf or abs(seq_idf[term] - par_idf[term]) > 1e-6:
            mismatches += 1
    print(f"  IDF mismatches: {mismatches}")

    # Search correctness
    random.seed(99)
    base_terms = list(seq_idf.keys())[:20]
    test_queries = [' '.join(random.choices(base_terms, k=random.randint(1, 5)))
                    for _ in range(50)]

    seq_search, _ = batch_search_sequential(test_queries, seq_result.index,
                                             top_k=10, documents=corpus)
    par_search, _ = batch_search_parallel(test_queries, par_result.index,
                                           top_k=10, num_workers=4, documents=corpus)

    search_match = all(
        len(s) == len(p) and all(
            sr.doc_id == pr.doc_id and abs(sr.score - pr.score) < 1e-6
            for sr, pr in zip(s, p)
        )
        for s, p in zip(seq_search, par_search)
    )
    print(f"  Search results match: {search_match}")

    # --- Performance test ---
    print("\n=== Index Speedup (5000 docs) ===")
    corpus5k = generate_corpus(5000, seed=77)

    t0 = time.perf_counter()
    build_tfidf_index_sequential(corpus5k)
    seq_t = time.perf_counter() - t0

    t0 = time.perf_counter()
    build_tfidf_index_parallel(corpus5k, num_workers=4)
    par_t = time.perf_counter() - t0

    print(f"  seq={seq_t:.2f}s  par={par_t:.2f}s  speedup={seq_t/par_t:.2f}x")

    print("\n=== Search Speedup (1000 queries) ===")
    idx = build_tfidf_index_parallel(corpus5k, num_workers=4)
    random.seed(202)
    base = ['machine', 'learning', 'algorithm', 'neural', 'network', 'database',
            'optimization', 'performance', 'clinical', 'trial