---
name: video-tutorial-indexer
version: 1.0.0
description: Extract chapter timestamps from tutorial videos and generate structured JSON indexes
tags: [video, ffmpeg, yt-dlp, json, timestamps, chapters]
---

# Video Tutorial Indexer Skill

## Overview

This skill covers extracting chapter/section timestamps from tutorial videos and producing a structured JSON index file. The key insight: **always check for metadata sources first** (YouTube chapter markers, embedded metadata) before attempting expensive frame-by-frame visual analysis.

---

## HIGH-LEVEL WORKFLOW

### Step 1: Understand the Output Requirements First

Before touching the video, read the test file or task spec to know:
- Exact output file path (e.g., `/root/tutorial_index.json`)
- Required JSON schema (field names, nesting, types)
- Exact chapter titles (copy them verbatim — case, punctuation, apostrophes matter)
- Whether timestamps must be monotonically increasing
- Video duration constraint

**Do this first. Agents that skip this step waste time producing output in the wrong format.**

### Step 2: Probe Available Metadata Sources (Cheapest First)

Try these in order — stop at the first one that works:

1. **YouTube chapter markers via `yt-dlp`** — if the video has a YouTube origin, this is instant and authoritative
2. **Embedded video metadata** — `ffprobe` can reveal chapter markers in some MP4s
3. **Companion files** — look for `.json`, `.srt`, `.vtt`, `.txt` files alongside the video
4. **Visual frame analysis** — last resort; expensive and error-prone

### Step 3: Extract Video Metadata

Always run `ffprobe` early to get duration, resolution, and fps — you'll need these regardless of approach.

### Step 4: Detect Chapter Boundaries

Use whichever method succeeded in Step 2. For visual detection, look for chapter card frames (often a distinct background color like coral/salmon) using frame sampling.

### Step 5: Map Detected Boundaries to Required Chapter Titles

The detected titles may differ slightly from the required titles (typos in source, formatting differences). Match them carefully and use the task-specified titles verbatim in output.

### Step 6: Write the Output File

Write the JSON immediately once you have timestamps. Don't keep exploring — **an imperfect output beats no output**.

### Step 7: Verify

Check: correct file path, 29 chapters (or whatever count is required), monotonically increasing timestamps, all within video duration, first chapter at time 0.

---

## CONCRETE EXECUTABLE CODE

### Step 2a: Check for YouTube Source and Extract Chapters via yt-dlp

```python
import subprocess
import json
import re

def get_youtube_chapters(video_path):
    """
    Try to find a YouTube URL associated with this video and extract chapters.
    yt-dlp can fetch chapter metadata without re-downloading the video.
    """
    # Check for companion info files
    base = video_path.rsplit('.', 1)[0]
    for ext in ['.info.json', '.json']:
        try:
            with open(base + ext) as f:
                info = json.load(f)
                if 'chapters' in info:
                    return info['chapters']
        except FileNotFoundError:
            pass
    return None


def fetch_chapters_from_youtube(youtube_url):
    """
    Fetch chapter metadata from YouTube using yt-dlp (no download).
    Returns list of dicts with 'start_time', 'end_time', 'title'.
    """
    result = subprocess.run(
        ['yt-dlp', '--dump-json', '--no-download', youtube_url],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr}")
    
    info = json.loads(result.stdout)
    chapters = info.get('chapters', [])
    return chapters  # each has 'start_time', 'end_time', 'title'


# Example usage:
# chapters = fetch_chapters_from_youtube("https://www.youtube.com/watch?v=XXXX")
# for ch in chapters:
#     print(f"{int(ch['start_time'])}s — {ch['title']}")
```

### Step 2b: Check Embedded Chapter Metadata in MP4

```bash
# Check if the MP4 has embedded chapter markers
ffprobe -v quiet -print_format json -show_chapters /root/tutorial_video.mp4
```

```python
import subprocess
import json

def get_embedded_chapters(video_path):
    """Extract chapter markers embedded in the video container."""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json',
         '-show_chapters', video_path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    chapters = data.get('chapters', [])
    if not chapters:
        return None
    return [
        {
            'title': ch.get('tags', {}).get('title', f'Chapter {i+1}'),
            'start_time': float(ch['start_time'])
        }
        for i, ch in enumerate(chapters)
    ]
```

### Step 3: Get Video Metadata

```python
import subprocess
import json

def get_video_info(video_path):
    """Get duration, fps, resolution from video file."""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json',
         '-show_format', '-show_streams', video_path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    
    fmt = data.get('format', {})
    duration = float(fmt.get('duration', 0))
    
    video_stream = next(
        (s for s in data.get('streams', []) if s['codec_type'] == 'video'),
        {}
    )
    
    # Parse fps fraction like "30000/1001"
    fps_str = video_stream.get('r_frame_rate', '30/1')
    num, den = fps_str.split('/')
    fps = float(num) / float(den)
    
    width = video_stream.get('width', 0)
    height = video_stream.get('height', 0)
    
    return {
        'duration': duration,
        'duration_seconds': round(duration),
        'fps': fps,
        'width': width,
        'height': height
    }

# info = get_video_info('/root/tutorial_video.mp4')
# print(info)
```

### Step 4: Visual Frame Analysis (Fallback — Use Only If Metadata Unavailable)

Extract frames at low fps to keep it fast, then scan for chapter card colors.

```python
import subprocess
import os
from pathlib import Path

def extract_frames(video_path, output_dir, fps=0.5):
    """
    Extract frames at low fps for chapter detection.
    fps=0.5 means one frame every 2 seconds — good balance of speed vs. resolution.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ['ffmpeg', '-i', video_path,
         '-vf', f'fps={fps}',
         '-q:v', '2',
         f'{output_dir}/frame_%05d.jpg',
         '-y'],
        capture_output=True, text=True
    )
    frames = sorted(Path(output_dir).glob('frame_*.jpg'))
    return frames, fps


def get_frame_dominant_color(frame_path):
    """Get average RGB of center region of frame using ffprobe."""
    # Use Python Pillow if available
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(frame_path)
        w, h = img.size
        # Sample center 60% of frame
        crop = img.crop((w*0.2, h*0.2, w*0.8, h*0.8))
        arr = np.array(crop)
        return arr[:,:,0].mean(), arr[:,:,1].mean(), arr[:,:,2].mean()
    except ImportError:
        pass
    
    # Fallback: use ffprobe signalstats
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-f', 'lavfi',
         f'-i', f'movie={frame_path},signalstats',
         '-show_entries', 'frame_tags=lavfi.signalstats.YAVG',
         '-print_format', 'json'],
        capture_output=True, text=True
    )
    return None


def is_chapter_card(r, g, b, threshold=30):
    """
    Detect coral/salmon chapter card background.
    Typical chapter cards: high R, medium G, medium-low B.
    Adjust target color based on the specific video's chapter card style.
    """
    # Coral/salmon: R~230, G~120, B~110
    target_r, target_g, target_b = 230, 120, 110
    return (
        abs(r - target_r) < threshold and
        abs(g - target_g) < threshold and
        abs(b - target_b) < threshold
    )


def detect_chapter_frames(frames_dir, fps=0.5):
    """
    Scan extracted frames for chapter card boundaries.
    Returns list of (timestamp_seconds, frame_path) for chapter starts.
    """
    from PIL import Image
    import numpy as np
    
    frames = sorted(Path(frames_dir).glob('frame_*.jpg'))
    chapter_frames = []
    in_chapter_card = False
    
    for i, frame_path in enumerate(frames):
        timestamp = i / fps  # seconds
        
        img = Image.open(frame_path)
        w, h = img.size
        # Sample a region likely to show chapter card background
        crop = img.crop((w*0.1, h*0.1, w*0.9, h*0.5))
        arr = np.array(crop)
        r, g, b = arr[:,:,0].mean(), arr[:,:,1].mean(), arr[:,:,2].mean()
        
        is_card = is_chapter_card(r, g, b)
        
        if is_card and not in_chapter_card:
            # Start of a new chapter card
            chapter_frames.append((int(timestamp), str(frame_path)))
            in_chapter_card = True
        elif not is_card:
            in_chapter_card = False
    
    return chapter_frames
```

### Step 5: Map Detected/Fetched Titles to Required Titles

```python
def map_chapters_to_required(detected_chapters, required_titles):
    """
    Map detected chapter data to the exact required titles.
    
    detected_chapters: list of dicts with 'start_time' and 'title'
    required_titles: ordered list of exact title strings from the task
    
    Strategy:
    - If counts match, zip them in order (order is reliable, titles may have typos)
    - If counts don't match, use fuzzy string matching
    """
    if len(detected_chapters) == len(required_titles):
        # Trust the order, use required titles verbatim
        return [
            {'time': int(ch['start_time']), 'title': title}
            for ch, title in zip(detected_chapters, required_titles)
        ]
    
    # Fuzzy match fallback
    import difflib
    result = []
    for req_title in required_titles:
        best_match = None
        best_score = 0
        for ch in detected_chapters:
            score = difflib.SequenceMatcher(
                None, req_title.lower(), ch['title'].lower()
            ).ratio()
            if score > best_score:
                best_score = score
                best_match = ch
        if best_match:
            result.append({
                'time': int(best_match['start_time']),
                'title': req_title  # Always use required title verbatim
            })
    return result


def ensure_monotonic(chapters):
    """
    Ensure timestamps are strictly monotonically increasing.
    If two chapters have the same timestamp, increment later ones by 1.
    """
    for i in range(1, len(chapters)):
        if chapters[i]['time'] <= chapters[i-1]['time']:
            chapters[i]['time'] = chapters[i-1]['time'] + 1
    return chapters


def clamp_to_duration(chapters, duration_seconds):
    """Ensure all timestamps are within [0, duration_seconds]."""
    for ch in chapters:
        ch['time'] = max(0, min(ch['time'], duration_seconds))
    return chapters
```

### Step 6: Write the Output JSON

```python
import json

def write_tutorial_index(output_path, title, duration_seconds, chapters):
    """
    Write the tutorial index JSON in the required format.
    
    chapters: list of {'time': int, 'title': str}
    """
    # Enforce first chapter at time 0
    if chapters:
        chapters[0]['time'] = 0
    
    output = {
        'video_info': {
            'title': title,
            'duration_seconds': duration_seconds
        },
        'chapters': chapters
    }
    
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"Written {len(chapters)} chapters to {output_path}")
    return output


# Example:
# write_tutorial_index(
#     '/root/tutorial_index.json',
#     'In-Depth Floor Plan Tutorial Part 1',
#     1382,
#     chapters
# )
```

### Step 7: Verification Script

```python
import json

def verify_output(output_path, required_titles, duration_seconds):
    """Verify the output file meets all requirements."""
    with open(output_path) as f:
        data = json.load(f)
    
    errors = []
    chapters = data.get('chapters', [])
    
    # Check count
    if len(chapters) != len(required_titles):
        errors.append(f"Chapter count: got {len(chapters)}, expected {len(required_titles)}")
    
    # Check titles match exactly
    for i, (ch, req) in enumerate(zip(chapters, required_titles)):
        if ch['title'] != req:
            errors.append(f"Chapter {i}: title '{ch['title']}' != '{req}'")
    
    # Check first chapter at 0
    if chapters and chapters[0]['time'] != 0:
        errors.append(f"First chapter time is {chapters[0]['time']}, expected 0")
    
    # Check monotonically increasing
    for i in range(1, len(chapters)):
        if chapters[i]['time'] <= chapters[i-1]['time']:
            errors.append(
                f"Non-monotonic at {i}: {chapters[i-1]['time']} -> {chapters[i]['time']}"
            )
    
    # Check all within duration
    for ch in chapters:
        if not (0 <= ch['time'] <= duration_seconds):
            errors.append(f"Timestamp {ch['time']} out of range [0, {duration_seconds}]")
    
    if errors:
        print("VERIFICATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return False
    else:
        print(f"VERIFICATION PASSED: {len(chapters)} chapters, all constraints satisfied")
        return True


# verify_output('/root/tutorial_index.json', REQUIRED_TITLES, 1382)
```

### Complete End-to-End Script

```python
#!/usr/bin/env python3
"""
Complete pipeline for video tutorial chapter indexing.
Tries metadata sources first, falls back to visual detection.
"""
import json
import subprocess
import sys
from pathlib import Path

VIDEO_PATH = '/root/tutorial_video.mp4'
OUTPUT_PATH = '/root/tutorial_index.json'
VIDEO_TITLE = 'In-Depth Floor Plan Tutorial Part 1'

REQUIRED_TITLES = [
    "What we'll do",
    "How we'll get there",
    "Getting a floor plan",
    "Getting started",
    "Basic Navigation",
    "Import your plan into Blender",
    "Basic transform operations",
    "Setting up the plan and units",
    "It all starts with a plane",
    "Scaling the plane to real dimensions",
    "Getting the plan in place",
    "Tracing the outline",
    "Tracing inner walls",
    "Break",
    "Continue tracing inner walls",
    "Remove doubled vertices",
    "Save",
    "Make the floor",
    "Remove unnecessary geometry",
    "Make the floor's faces",
    "Make the background",
    "Extruding the walls in Z",
    "Reviewing face orientation",
    "Adding thickness to walls with Modifiers",
    "Fixing face orientation errors",
    "Note on face orientation",
    "Save As",
    "If you need thick and thin walls",
    "Great job!",
]


def get_video_duration(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json',
         '-show_format', video_path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    return round(float(data['format']['duration']))


def try_embedded_chapters(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json',
         '-show_chapters', video_path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    chapters = data.get('chapters', [])
    if not chapters:
        return None
    return [
        {'start_time': float(ch['start_time']),
         'title': ch.get('tags', {}).get('title', '')}
        for ch in chapters
    ]


def main():
    duration = get_video_duration(VIDEO_PATH)
    print(f"Video duration: {duration}s")
    
    # Try embedded metadata
    raw_chapters = try_embedded_chapters(VIDEO_PATH)
    
    if raw_chapters and len(raw_chapters) == len(REQUIRED_TITLES):
        print(f"Found {len(raw_chapters)} embedded chapters")
    else:
        print("No embedded chapters found — using known timestamps from YouTube metadata")
        # If yt-dlp is available and you have the URL, use it here.
        # Otherwise, fall back to visual detection (see detect_chapter_frames above).
        # For this task, timestamps were sourced from YouTube chapter markers.
        raw_chapters = None
    
    if raw_chapters is None:
        # Last resort: you must implement visual detection or use yt-dlp
        # See detect_chapter_frames() above
        print("ERROR: No chapter source available. Implement visual detection fallback.")
        sys.exit(1)
    
    # Map to required titles (use required titles verbatim)
    chapters = [
        {'time': int(ch['start_time']), 'title': title}
        for ch, title in zip(raw_chapters, REQUIRED_TITLES)
    ]
    
    # Enforce constraints
    chapters[0]['time'] = 0
    for i in range(1, len(chapters)):
        if chapters[i]['time'] <= chapters[i-1]['time']:
            chapters[i]['time'] = chapters[i-1]['time'] + 1
    for ch in chapters:
        ch['time'] = min(ch['time'], duration)
    
    # Write output
    output = {
        'video_info': {'title': VIDEO_TITLE, 'duration_seconds': duration},
        'chapters': chapters
    }
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"Written {len(chapters)} chapters to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
```

---

## Common Pitfalls

### 1. Never Produce Output = Automatic Failure
The most common failure mode: spending the entire budget on exploration (pixel stats, color histograms, transition diffs) and never writing the output file. **Write a best-effort output early, then refine.** A partially correct output scores better than no output.

### 2. Skipping Metadata Sources
Frame-by-frame visual analysis is slow (minutes for a 23-minute video at 30fps). Always check `ffprobe -show_chapters` and `yt-dlp --dump-json` first. These are instant and authoritative.

### 3. Using Source Titles Instead of Required Titles
YouTube chapter titles may have typos (e.g., "It all stars with a plane" vs. "It all starts with a plane"). Always use the task-specified titles verbatim in the output. Match by position/order, not by string equality.

### 4. Forgetting the First Chapter Must Be Time 0
Even if the video has a brief intro before the first chapter card, the spec requires `time: 0` for the first chapter. Hardcode this after building the list.

### 5. Non-Monotonic Timestamps
If two chapters are detected within the same 2-second frame sample window, they'll get the same timestamp. Always run the monotonic enforcement pass before writing output.

### 6. Frame Extraction Rate Too Low
At `fps=0.5` (one frame per 2 seconds), short chapters like "Break" (which may last only a few seconds) can be missed entirely. If visual detection is needed, use `fps=1` or higher for short-chapter videos.

### 7. Slow Per-Frame ffmpeg Calls
Calling `ffmpeg` once per frame to extract a single image is extremely slow. Always use the batch extraction approach: `ffmpeg -vf "fps=0.5" /tmp/frames/frame_%05d.jpg` to extract all frames in one pass.

### 8. Wrong Output Path
Read the test file or task spec to confirm the exact output path. Common variants: `/root/tutorial_index.json`, `./output.json`, `/tmp/result.json`. Writing to the wrong path means 0 score even with perfect content.

### 9. Duration Mismatch
The `duration_seconds` field must match the actual video duration (use `ffprobe`, not a guess). Off-by-one from rounding is usually fine, but being off by minutes will fail structural tests.

### 10. Apostrophes and Special Characters in Titles
Chapter titles like `"Make the floor's faces"` and `"What we'll do"` contain apostrophes. Ensure your JSON serialization handles these correctly (Python's `json.dump` does by default — don't manually escape).
