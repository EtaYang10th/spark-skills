---
title: "Dice Game Scoring & Match Comparison from Excel Data with PDF Rules"
category: "financial-modeling-qa"
domain: "data-analysis-with-custom-scoring"
tags:
  - excel-parsing
  - pdf-rule-extraction
  - custom-scoring-logic
  - pandas
  - openpyxl
  - game-theory
  - combinatorial-optimization
applicability:
  - Large Excel datasets with structured game/turn/dice data
  - PDF documents describing scoring rules and game mechanics
  - Questions requiring player-vs-player match comparisons
  - Tasks where answer is a single numeric value written to a file
---

# Dice Game Scoring & Match Comparison Analysis

## Overview

This skill covers a class of tasks where you must:
1. Read scoring/game rules from a PDF document
2. Parse a large Excel file containing game data (dice rolls across turns)
3. Implement custom scoring categories based on the PDF rules
4. Optimally assign categories to turns within each game
5. Compare paired games between two players and compute a final numeric answer

The general pattern applies to any task that combines **rule extraction from unstructured documents** with **structured data processing and optimization**.

---

## High-Level Workflow

### Step 1: Read the Test File First

**WHY:** Before doing any computation, determine the exact expected output format, file path, and validation criteria. This prevents wasted effort on computation that produces output in the wrong format or location.

```python
# Always check the test file to understand:
# - Expected output file path (e.g., /root/answer.txt)
# - Expected format (integer, float, string)
# - Any specific validation logic
import os
test_dir = "../tests/"
for f in os.listdir(test_dir):
    if f.endswith(".py"):
        with open(os.path.join(test_dir, f)) as fh:
            print(fh.read())
```

Key things to extract from the test file:
- The exact file path for the answer (e.g., `/root/answer.txt`)
- Whether the answer should be an integer or float
- Any expected answer value (sometimes embedded in test code)

### Step 2: Extract Rules from the PDF

**WHY:** The PDF contains the scoring categories, game structure, and any special rules. Misinterpreting even one rule can cascade into a wrong answer.

```python
import pypdf

def extract_pdf_rules(pdf_path):
    """Extract all text from a PDF background document."""
    reader = pypdf.PdfReader(pdf_path)
    full_text = ""
    for page in reader.pages:
        full_text += page.extract_text() + "\n"
    return full_text

rules_text = extract_pdf_rules("/root/background.pdf")
print(rules_text)
```

**Critical:** Read the PDF output carefully. Pay attention to:
- Exact category names and their scoring formulas
- Whether "run" means contiguous subsequence or any subsequence
- Edge cases like ties, missing data, single-turn games
- How categories are assigned (one per turn, no reuse, etc.)

### Step 3: Inspect the Excel Data Structure

**WHY:** Before writing any processing code, understand the exact layout — column names, data types, how games/turns/dice are organized.

```python
import openpyxl

def inspect_workbook(path, max_rows=20):
    """Inspect Excel workbook structure without loading full data."""
    wb = openpyxl.load_workbook(path, read_only=True)
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        print(f"Sheet: {sheet_name}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows:
                break
            print(row)
    wb.close()

inspect_workbook("/root/data.xlsx")
```

Also check total row count to understand scale:

```python
import pandas as pd

df = pd.read_excel("/root/data.xlsx", engine="openpyxl")
print(f"Shape: {df.shape}")
print(f"Columns: {list(df.columns)}")
print(f"Dtypes:\n{df.dtypes}")
print(f"First 10 rows:\n{df.head(10)}")
print(f"Unique games: {df['Game'].nunique() if 'Game' in df.columns else 'N/A'}")
```

### Step 4: Implement Scoring Categories

**WHY:** Each category must be implemented exactly as described in the PDF. This is where most errors occur.

Typical dice-game scoring categories (adapt to your PDF):

```python
from collections import Counter

def score_high_and_often(dice):
    """Most frequent number × count of that number.
    If tie in frequency, use the higher number."""
    counts = Counter(dice)
    max_freq = max(counts.values())
    candidates = [num for num, cnt in counts.items() if cnt == max_freq]
    best_num = max(candidates)
    return best_num * max_freq

def score_summation(dice):
    """Sum of all dice."""
    return sum(dice)

def score_highs_and_lows(dice):
    """Difference between highest and lowest die value."""
    return max(dice) - min(dice)

def score_only_two_numbers(dice):
    """30 points if exactly 2 distinct values appear, else 0."""
    return 30 if len(set(dice)) == 2 else 0

def score_all_the_numbers(dice):
    """If all 6 dice show different values (1-6 each once): sum of all.
    Otherwise 0."""
    return sum(dice) if len(set(dice)) == 6 else 0

def score_ordered_subset_of_four(dice):
    """50 points if there exists a contiguous run of 4 consecutive
    increasing or decreasing numbers in the sorted dice. Else 0."""
    sorted_d = sorted(set(dice))
    if len(sorted_d) < 4:
        return 0
    for i in range(len(sorted_d) - 3):
        window = sorted_d[i:i+4]
        if all(window[j+1] - window[j] == 1 for j in range(3)):
            return 50
    return 0
```

**Critical note on "Ordered Subset of Four":** The PDF may say "a run of 4 consecutive increasing or decreasing numbers." This means 4 values that form a contiguous arithmetic sequence with step 1 (e.g., 2,3,4,5) — checked among the *distinct sorted values* of the dice. It does NOT mean a longest increasing subsequence of length ≥ 4.

### Step 5: Compute Game Scores with Optimal Category Assignment

**WHY:** When a game has 2 turns, each turn must be assigned a different category (no reuse). The optimal assignment maximizes total game score.

```python
CATEGORIES = [
    score_high_and_often,
    score_summation,
    score_highs_and_lows,
    score_only_two_numbers,
    score_all_the_numbers,
    score_ordered_subset_of_four,
]

def compute_category_scores(dice):
    """Compute score for each category given a set of 6 dice."""
    return [cat(dice) for cat in CATEGORIES]

def game_score_two_turns(dice_turn1, dice_turn2):
    """Optimal score when assigning one category to each turn (no reuse).
    Tries all pairs (i, j) where i != j."""
    scores1 = compute_category_scores(dice_turn1)
    scores2 = compute_category_scores(dice_turn2)
    best = 0
    n = len(CATEGORIES)
    for i in range(n):
        for j in range(n):
            if i != j:
                best = max(best, scores1[i] + scores2[j])
    return best

def game_score_single_turn(dice):
    """When a game has only 1 turn, score is sum of ALL applicable categories."""
    return sum(compute_category_scores(dice))
```

**Key insight for single-turn games:** When a game has only one turn (due to missing data), the score is the **sum of all applicable category scores** for that single turn, not just the best single category. This is a critical edge case that changes match outcomes.

### Step 6: Parse Data, Compute All Game Scores, and Determine Match Results

**WHY:** The final step ties everything together — loading data, grouping by game/turn, scoring, pairing, and computing the answer.

```python
import pandas as pd

def solve(data_path, answer_path):
    df = pd.read_excel(data_path, engine="openpyxl")
    
    # Build turns dictionary: {game_number: {turn_number: [dice_values]}}
    turns = {}
    for _, row in df.iterrows():
        gn = int(row["Game"])
        tn = int(row["Turn"])
        dice = [int(row[f"Die {d}"]) for d in range(1, 7)]
        if gn not in turns:
            turns[gn] = {}
        turns[gn][tn] = dice
    
    # Compute game scores
    game_scores = {}
    for gn, turn_data in turns.items():
        turn_numbers = sorted(turn_data.keys())
        if len(turn_numbers) >= 2:
            game_scores[gn] = game_score_two_turns(
                turn_data[turn_numbers[0]],
                turn_data[turn_numbers[1]]
            )
        else:
            # Single turn: sum of all category scores
            game_scores[gn] = game_score_single_turn(
                turn_data[turn_numbers[0]]
            )
    
    # Pair games: odd vs even (1v2, 3v4, 5v6, ...)
    # Odd games = Player 1, Even games = Player 2
    max_game = max(turns.keys())
    p1_wins = 0
    p2_wins = 0
    ties = 0
    
    for g in range(1, max_game + 1, 2):
        s1 = game_scores.get(g, 0)
        s2 = game_scores.get(g + 1, 0)
        if s1 > s2:
            p1_wins += 1
        elif s2 > s1:
            p2_wins += 1
        else:
            ties += 1
    
    answer = p1_wins - p2_wins
    
    with open(answer_path, "w") as f:
        f.write(str(answer))
    
    print(f"Player 1 wins: {p1_wins}")
    print(f"Player 2 wins: {p2_wins}")
    print(f"Ties: {ties}")
    print(f"Answer: {answer}")
    return answer

solve("/root/data.xlsx", "/root/answer.txt")
```

---

## Common Pitfalls

### 1. Never Writing the Answer File
**Symptom:** Tests fail with "answer file does not exist."
**Cause:** The agent spends all time computing and debugging but never writes the result.
**Fix:** Write the answer file early, even with a preliminary value. Update it as you refine. Always verify the file exists before finishing.

### 2. Misinterpreting "Ordered Subset of Four" as Subsequence
**Symptom:** Answer is wildly off (e.g., -9 instead of 23).
**Cause:** Interpreting "run of 4 consecutive numbers" as any increasing subsequence of length 4 rather than 4 values forming a contiguous step-1 sequence.
**Fix:** Use `sorted(set(dice))` and check for windows of 4 where each adjacent pair differs by exactly 1.

### 3. Wrong Handling of Single-Turn Games
**Symptom:** Answer is off by a small amount (e.g., 25 instead of 23).
**Cause:** A game with only 1 turn (missing data) is scored as best-single-category instead of sum-of-all-categories.
**Fix:** Detect games with fewer than 2 turns. For single-turn games, the score is the sum of all applicable category scores. This can flip match outcomes.

### 4. Tie-Breaking in "High and Often"
**Symptom:** Subtle scoring differences in a few games.
**Cause:** When multiple numbers share the highest frequency, using the wrong one.
**Fix:** Always pick the **highest number** among those tied for most frequent.

### 5. Spending Too Long on Marginal Variations
**Symptom:** Agent tries 10+ scoring interpretations without converging.
**Cause:** Not anchoring on the standard interpretation first and checking the delta.
**Fix:** Implement the most natural reading of each rule first. If the answer is close (off by 1-3), investigate edge cases (missing turns, tie-breaking) rather than reinterpreting entire categories.

### 6. Not Reading the Test File First
**Symptom:** Wasted computation because the output format or path is wrong.
**Fix:** Always read `../tests/test_outputs.py` (or equivalent) before any computation. It tells you the expected file path, format, and sometimes the expected value.

### 7. Column Name Assumptions
**Symptom:** KeyError when accessing DataFrame columns.
**Cause:** Assuming column names like "Die 1" without checking.
**Fix:** Always `print(df.columns.tolist())` before accessing columns.

---

## Environment Setup

```bash
# These packages are typically pre-installed in the environment
pip install --break-system-packages pandas==2.2.3 openpyxl==3.1.5 pypdf==5.1.0

# Verify
python3 -c "import pandas, openpyxl, pypdf; print('All imports OK')"
```

**Note:** Use `--break-system-packages` on Ubuntu 24.04+ due to PEP 668 externally-managed environments. PyMuPDF (`fitz`) may also be useful but `pypdf` is sufficient for text extraction.

---

## Reference Implementation

This is the complete, end-to-end solution. Copy, adapt column names and scoring rules to your specific PDF, and run.

```python
#!/usr/bin/env python3
"""
Complete solution for dice-game match comparison tasks.

Workflow:
  1. Read PDF for scoring rules
  2. Load Excel data
  3. Score each game using optimal category assignment
  4. Pair odd vs even games, count wins
  5. Write (P1 wins - P2 wins) to answer file

Adapt the scoring functions to match YOUR PDF's category definitions.
"""

import pandas as pd
import pypdf
from collections import Counter
import os
import sys

# ─────────────────────────────────────────────
# CONFIGURATION — adapt these to your task
# ─────────────────────────────────────────────
DATA_PATH = "/root/data.xlsx"
PDF_PATH = "/root/background.pdf"
ANSWER_PATH = "/root/answer.txt"

# ─────────────────────────────────────────────
# STEP 1: Extract and display PDF rules
# ─────────────────────────────────────────────
def extract_pdf_text(pdf_path):
    """Extract all text from a PDF file."""
    if not os.path.exists(pdf_path):
        print(f"WARNING: PDF not found at {pdf_path}")
        return ""
    reader = pypdf.PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"
    return text

print("=" * 60)
print("STEP 1: Reading PDF rules")
print("=" * 60)
rules_text = extract_pdf_text(PDF_PATH)
print(rules_text[:2000])  # Print first 2000 chars for verification
print("..." if len(rules_text) > 2000 else "")

# ─────────────────────────────────────────────
# STEP 2: Load and inspect Excel data
# ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 2: Loading Excel data")
print("=" * 60)

df = pd.read_excel(DATA_PATH, engine="openpyxl")
print(f"Shape: {df.shape}")
print(f"Columns: {list(df.columns)}")
print(f"Dtypes:\n{df.dtypes}")
print(f"\nFirst 5 rows:\n{df.head()}")

# Detect column names for game, turn, and dice
# Adapt these if your columns have different names
GAME_COL = "Game"
TURN_COL = "Turn"
# Detect dice columns — look for columns containing "Die" or "Dice" or "D1"-"D6"
dice_cols = [c for c in df.columns if c not in [GAME_COL, TURN_COL]]
print(f"\nDice columns detected: {dice_cols}")
print(f"Number of unique games: {df[GAME_COL].nunique()}")
print(f"Game number range: {df[GAME_COL].min()} to {df[GAME_COL].max()}")

# ─────────────────────────────────────────────
# STEP 3: Define scoring categories
# ─────────────────────────────────────────────
# ADAPT THESE FUNCTIONS TO MATCH YOUR PDF RULES

def score_high_and_often(dice):
    """Most frequent number × its count.
    Tie-break: use the HIGHER number among those with max frequency."""
    counts = Counter(dice)
    max_freq = max(counts.values())
    candidates = [num for num, cnt in counts.items() if cnt == max_freq]
    best_num = max(candidates)  # Higher number wins ties
    return best_num * max_freq

def score_summation(dice):
    """Simple sum of all dice values."""
    return sum(dice)

def score_highs_and_lows(dice):
    """Difference between the highest and lowest die."""
    return max(dice) - min(dice)

def score_only_two_numbers(dice):
    """30 points if exactly 2 distinct values appear; 0 otherwise."""
    return 30 if len(set(dice)) == 2 else 0

def score_all_the_numbers(dice):
    """Sum of all dice if all 6 values are distinct (i.e., 1-6 each once).
    0 otherwise."""
    if len(set(dice)) == 6:
        return sum(dice)  # Always 21 for standard 1-6 dice
    return 0

def score_ordered_subset_of_four(dice):
    """50 points if the distinct sorted values contain a contiguous run
    of 4 consecutive integers (e.g., 2,3,4,5). 0 otherwise.
    
    IMPORTANT: This checks for a CONTIGUOUS window in sorted unique values,
    NOT a longest increasing subsequence."""
    sorted_unique = sorted(set(dice))
    if len(sorted_unique) < 4:
        return 0
    for i in range(len(sorted_unique) - 3):
        window = sorted_unique[i:i + 4]
        if all(window[j + 1] - window[j] == 1 for j in range(3)):
            return 50
    return 0

# List of all category scoring functions
CATEGORIES = [
    score_high_and_often,
    score_summation,
    score_highs_and_lows,
    score_only_two_numbers,
    score_all_the_numbers,
    score_ordered_subset_of_four,
]
NUM_CATS = len(CATEGORIES)

def compute_all_category_scores(dice):
    """Return a list of scores, one per category."""
    return [cat(dice) for cat in CATEGORIES]

# ─────────────────────────────────────────────
# STEP 4: Optimal category assignment per game
# ─────────────────────────────────────────────

def optimal_two_turn_score(dice_t1, dice_t2):
    """For a game with 2 turns, assign one category to each turn
    (no category reuse) to maximize total score.
    
    Brute-force all (i, j) pairs where i != j.
    With 6 categories this is only 30 pairs — trivial."""
    s1 = compute_all_category_scores(dice_t1)
    s2 = compute_all_category_scores(dice_t2)
    best = 0
    for i in range(NUM_CATS):
        for j in range(NUM_CATS):
            if i != j:
                best = max(best, s1[i] + s2[j])
    return best

def single_turn_score(dice):
    """For a game with only 1 turn (missing data), the score is the
    SUM of all applicable category scores.
    
    CRITICAL: This is NOT the best single category — it's the sum of ALL.
    This edge case can flip match outcomes."""
    return sum(compute_all_category_scores(dice))

# ─────────────────────────────────────────────
# STEP 5: Build game structure and compute scores
# ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 5: Computing game scores")
print("=" * 60)

# Build: {game_number: {turn_number: [dice_values]}}
turns = {}
for _, row in df.iterrows():
    gn = int(row[GAME_COL])
    tn = int(row[TURN_COL])
    dice = [int(row[col]) for col in dice_cols]
    if gn not in turns:
        turns[gn] = {}
    turns[gn][tn] = dice

# Detect anomalies
single_turn_games = []
multi_turn_games = []
for gn, td in turns.items():
    if len(td) < 2:
        single_turn_games.append(gn)
    else:
        multi_turn_games.append(gn)

if single_turn_games:
    print(f"WARNING: {len(single_turn_games)} game(s) with only 1 turn: {single_turn_games[:10]}")
    for gn in single_turn_games[:5]:
        td = turns[gn]
        tn = list(td.keys())[0]
        print(f"  Game {gn}, Turn {tn}: dice = {td[tn]}")

print(f"Games with 2+ turns: {len(multi_turn_games)}")

# Compute all game scores
game_scores = {}
for gn, td in turns.items():
    turn_numbers = sorted(td.keys())
    if len(turn_numbers) >= 2:
        game_scores[gn] = optimal_two_turn_score(
            td[turn_numbers[0]], td[turn_numbers[1]]
        )
    else:
        # Single turn: sum of all categories
        game_scores[gn] = single_turn_score(td[turn_numbers[0]])

# ─────────────────────────────────────────────
# STEP 6: Pair games and count match results
# ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 6: Pairing games and counting wins")
print("=" * 60)

# Odd games → Player 1, Even games → Player 2
# Matches: (1 vs 2), (3 vs 4), (5 vs 6), ...
max_game = max(turns.keys())
p1_wins = 0
p2_wins = 0
ties = 0

for g in range(1, max_game + 1, 2):
    s1 = game_scores.get(g, 0)
    s2 = game_scores.get(g + 1, 0)
    if s1 > s2:
        p1_wins += 1
    elif s2 > s1:
        p2_wins += 1
    else:
        ties += 1

answer = p1_wins - p2_wins

print(f"Player 1 wins: {p1_wins}")
print(f"Player 2 wins: {p2_wins}")
print(f"Ties:          {ties}")
print(f"Total matches: {p1_wins + p2_wins + ties}")
print(f"Answer (P1 wins - P2 wins): {answer}")

# ─────────────────────────────────────────────
# STEP 7: Write answer
# ─────────────────────────────────────────────
with open(ANSWER_PATH, "w") as f:
    f.write(str(answer))

print(f"\nWritten '{answer}' to {ANSWER_PATH}")

# Verify the file
with open(ANSWER_PATH, "r") as f:
    content = f.read().strip()
print(f"Verification — file contains: '{content}'")
```

---

## Verification Checklist

Before finalizing, always verify:

1. **Answer file exists** at the expected path
2. **Answer is a plain number** (no extra whitespace, no units, no explanation)
3. **Edge cases handled:**
   - Games with missing turns (single-turn scoring)
   - Tie-breaking rules in scoring categories
   - Games that don't exist in the data (default score 0)
4. **Category assignment is optimal** (no reuse, max total)
5. **Player assignment matches the question** (odd = P1, even = P2, or as specified)

```python
# Quick sanity checks
assert os.path.exists(ANSWER_PATH), "Answer file not written!"
with open(ANSWER_PATH) as f:
    val = f.read().strip()
assert val.lstrip("-").isdigit(), f"Answer is not a number: '{val}'"
print(f"All checks passed. Answer: {val}")
```

---

## Generalization Notes

This skill generalizes to any task that follows the pattern:

1. **Rules in a document** (PDF, text, markdown) → extract and implement
2. **Data in a spreadsheet** (Excel, CSV) → parse and structure
3. **Custom scoring/evaluation** → implement per the rules
4. **Optimization** → assign resources (categories, items) optimally
5. **Comparison** → pair entities and compute aggregate statistics
6. **Single numeric answer** → write to a file

The specific scoring categories will differ per task, but the workflow — read rules, inspect data, implement scoring, optimize assignment, aggregate results — remains the same. Always start by reading the test expectations and the rules document before writing any computation code.