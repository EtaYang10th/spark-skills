---
title: "Parallel TF-IDF Search Engine — Parallelizing Sequential Python with multiprocessing"
category: "parallel-tfidf-search"
domain: "python-parallelism"
tags:
  - multiprocessing
  - tfidf
  - parallel-indexing
  - parallel-search
  - speedup
  - inverted-index
applicability:
  - Given a single-threaded Python TF-IDF search engine, parallelize both indexing and query search
  - Achieve measurable speedup (typically 1.5x+ indexing, 2x+ search) on multi-core systems
  - Produce results identical to the sequential baseline
---

# Parallel TF-IDF Similarity Search — Agent Skill

## 1. High-Level Workflow

### Step 1: Read the test file FIRST

Before touching any code, read the test file (usually at `/root/tests/test_outputs.py`) to understand:
- The exact function signatures expected (`build_tfidf_index_parallel`, `batch_search_parallel`)
- The return types expected (`ParallelIndexingResult`, `SearchResult`, `TFIDFIndex`)
- The exact speedup thresholds (e.g., 1.5x for indexing, 2x for search)
- The corpus size used for benchmarking (affects whether parallelism overhead dominates)
- How correctness is checked (IDF value matching, search result matching)

This prevents wasted iterations guessing at API shapes.

### Step 2: Read the sequential implementation completely

Read every line of `sequential.py`. Identify:
- Data structures: `TFIDFIndex` (with `doc_vectors`, `doc_norms`, `inverted_index`, `idf`, `document_frequencies`, `vocabulary`, `num_documents`)
- The indexing pipeline stages and their computational complexity
- The search/scoring algorithm (typically cosine similarity via dot product with inverted index)
- Any helper classes (`Document`, `SearchResult`, `ParallelIndexingResult`)

Typical sequential indexing pipeline:
1. Tokenize + compute term frequencies per document — O(total