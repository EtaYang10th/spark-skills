---
name: financial-modeling-qa-bowling-scoring
version: 1.0.0
task_category: financial-modeling-qa
tags: [data-analysis, excel, scoring-rules, game-theory, pandas, openpyxl]
description: >
  Procedural skill for analyzing structured game/sports scoring data from Excel files,
  applying domain-specific scoring rules from a PDF background document, and computing
  comparative statistics between two players or strategies.
---

# Module 1: Data Ingestion and Structural Inspection

## 1.1 Always Inspect Before Assuming

Before writing any scoring logic, fully characterize the workbook:

```python
import openpyxl
import pandas as pd

wb = openpyxl.load_workbook('/root/data.xlsx')
print("Sheets:", wb.sheetnames)

ws = wb.active  # or wb['Sheet1']
print("Dimensions:", ws.dimensions)
print("Max row:", ws.max_row, "Max col:", ws.max_column)

# Print first few rows to understand column layout
for i, row in enumerate(ws.iter_rows(values_only=True)):
    if i < 5:
        print(f"Row {i+1}: {row}")
```

## 1.2 Detect Hidden or Anomalous Rows

Data files in this domain often contain hidden metadata or embedded corrections in
unexpected columns. Always scan ALL columns, not just the expected range:

```python
# Flag rows that have data beyond the expected column range
expected_cols = 10  # adjust based on your schema
anomalous = []
for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
    if any(cell is not None for cell in row[expected_cols:]):
        anomalous.append((row_idx, row))
        print(f"Row {row_idx}: {row}")
```

Key insight: anomalous rows may encode missing or corrected records in their
extra columns. Parse them as supplementary data, not noise.

## 1.3 Read the Background PDF for Scoring Rules

Never assume scoring rules — always extract them from the PDF:

```python
# Using pypdf (pre-installed)
from pypdf import PdfReader

reader = PdfReader('/root/background.pdf')
for page in reader.pages:
    print(page.extract_text())
```

If `pypdf` gives poor results, install `pymupdf`:
```bash
pip install pymupdf --break-system-packages -q
```
```python
import fitz
doc = fitz.open('/root/background.pdf')
for page in doc:
    print(page.get_text())
```

Also check for a "Formats" or "Rules" sheet in the workbook itself.

---

# Module 2: Scoring Logic and Player Assignment

## 2.1 Reconstruct Missing Records from Anomalous Rows

If a row contains data in unexpected columns, treat it as an embedded record:

```python
# Example: row has normal data in cols 0-9 and hidden record in cols 13-20
# Parse the hidden segment according to the same schema as normal rows
hidden_record = {
    'turn_num': row[13],
    'game_num': row[14],
    'rolls': list(row[15:21])
}
# Insert into your dataframe at the correct position
```

Always verify the recovered record fills a gap (e.g., a missing turn number
in a game that otherwise appears incomplete).

## 2.2 Apply Scoring Rules Precisely

Implement scoring as a pure function per turn/frame, then aggregate per game:

```python
def score_turn(rolls, turn_num, total_turns):
    """
    Apply domain-specific scoring rules.
    Read rules from PDF — do NOT guess.
    Common patterns: highest-value multipliers, sum rules, bonus frames.
    """
    # Example structure — replace with actual rules from PDF
    score = 0
    # ... rule logic ...
    return score

def score_game(turns):
    """Aggregate turn scores for a complete game."""
    return sum(score_turn(t['rolls'], t['turn_num'], len(turns)) for _, t in turns.iterrows())
```

## 2.3 Player Assignment and Match Pairing

For odd/even game → player assignment:
- Odd-numbered games → Player 1
- Even-numbered games → Player 2
- Matches are paired sequentially: (game 1 vs game 2), (game 3 vs game 4), ...

```python
game_scores = {}  # {game_num: total_score}

# Compute all game scores first, then pair
p1_wins = 0
p2_wins = 0
ties = 0

game_nums = sorted(game_scores.keys())
# Pair consecutive games
for i in range(0, len(game_nums) - 1, 2):
    g_odd = game_nums[i]    # odd game → Player 1
    g_even = game_nums[i+1] # even game → Player 2
    s1 = game_scores[g_odd]
    s2 = game_scores[g_even]
    if s1 > s2:
        p1_wins += 1
    elif s2 > s1:
        p2_wins += 1
    else:
        ties += 1

result = p1_wins - p2_wins
```

---

# Module 3: Verification and Output

## 3.1 Sanity Checks Before Writing Answer

```python
# Verify game count is even (all games have a pair)
assert len(game_nums) % 2 == 0, "Unpaired game detected"

# Verify no game has zero score (likely incomplete parsing)
for g, s in game_scores.items():
    assert s > 0, f"Game {g} has zero score — check for missing turns"

# Verify total matches = p1_wins + p2_wins + ties
total_matches = len(game_nums) // 2
assert p1_wins + p2_wins + ties == total_matches
```

## 3.2 Write Answer

```python
answer = p1_wins - p2_wins
with open('/root/answer.txt', 'w') as f:
    f.write(str(answer))

# Verify
with open('/root/answer.txt') as f:
    print("Answer:", f.read())
```

---

# Common Pitfalls

1. **Ignoring anomalous/hidden columns**: Extra data beyond the expected column
   range is almost always meaningful — a missing record, a correction, or metadata.
   Always scan the full row width.

2. **Skipping the PDF**: Scoring rules in this domain are non-trivial and
   domain-specific. Guessing or using generic rules leads to wrong answers.
   Read the PDF first, every time.

3. **Wrong player-to-game mapping**: Odd game = Player 1, even game = Player 2.
   Do not reverse this. Verify by checking game numbers explicitly, not by
   DataFrame index position.

4. **Incomplete games silently scoring as zero or partial**: If a game is missing
   turns, its score will be wrong. Always verify each game has the expected number
   of turns before scoring. Recover missing turns from anomalous rows.

5. **Pairing by index instead of game number**: Always sort by actual game number
   and pair on that, not on DataFrame row order, which may differ after filtering.

6. **Not verifying the answer file format**: The test checks that the answer is
   a plain number. Do not write explanatory text — write only the integer.
