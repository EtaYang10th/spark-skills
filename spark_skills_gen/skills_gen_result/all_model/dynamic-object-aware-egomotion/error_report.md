# Error Report

## Attempt 1 — FAIL

Commands:     k2,d2=orb.detectAndCompute(g2,None)
    H=np.eye(3,dtype=np.float32)
    if d1 is not None and d2 is not None:
        bf=cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches=sorted(bf.match(d1,d2), key=lambda m:m.distance)[:400]
        p1=np.float32([k1[m.queryIdx].pt for m in matches])
        p2=np.float32([k2[m.trainIdx].pt for m in matches])
        if len(p1)>=8:
            HH,mask=cv2.findHomography(p2,p1,cv2.RANSAC,4.0)
            if HH is not None: H=HH
    warp=cv2.w


## Attempt 2 — FAIL

Commands: -            valid = cv2.warpPerspective(ones, H, (ws, hs), flags=cv2.INTER_NEAREST) > 0
-            bg_frames.append(warped.astype(np.float32))
-            valid_maps.append(valid)
-        if bg_frames:
-            stack = np.stack(bg_frames, axis=0)
-            valid_stack = np.stack(valid_maps, axis=0)
-            median_bg = np.median(stack, axis=0)
-            valid_count = valid_stack.sum(axis=0)
-            color_diff = np.max(np.abs(curr.astype(np.float32) - median_bg), axis=2)
-


## Attempt 3 — FAIL

Commands:             gray_stack = np.stack([x[0] for x in aligned_stack], axis=0)
            rgb_stack = np.stack([x[1] for x in aligned_stack], axis=0)
            val_stack = np.stack([x[2] for x in aligned_stack], axis=0)
            median_gray = np.median(gray_stack, axis=0).astype(np.uint8)
            median_rgb = np.median(rgb_stack, axis=0).astype(np.int16)
            valid_med = val_stack.sum(axis=0) >= 2
            med_gray_diff = cv2.absdiff(curr_gray, median_gray)
            med_rgb_diff


## Attempt 4 — FAIL

Commands: +    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in small_frames]
     blur_grays = [cv2.GaussianBlur(g, (5, 5), 0) for g in grays]
@@
-    for i in range(len(frames)):
-        current = frames[i]
+    sx = work_w / float(w)
+    sy = work_h / float(h)
+
+    for i in range(len(frames)):
+        current = small_frames[i]
         current_gray = blur_grays[i]
-        union = np.zeros((h, w), dtype=bool)
+        union = np.zeros((work_h, work_w), dtype=bool)
@@
-            M = local_af


## Attempt 5 — FAIL

Commands:         arrays[f'f_{i}_data'] = data
        arrays[f'f_{i}_indices'] = indices
        arrays[f'f_{i}_indptr'] = indptr
    np.savez_compressed(OUT_NPZ, **arrays)


def main():
    frames, _, _ = sample_video(VIDEO_PATH, SAMPLE_FPS)
    frames_small = [cv2.resize(frame, (SMALL_W, SMALL_H), interpolation=cv2.INTER_AREA) for frame in frames]
    grays, affines, motions = estimate_pairwise_affines(frames_small)
    labels = classify_motion(motions)
    transforms, stabilized = build_stabilized_fra


## Final Exploration Memo

## Exploration Memo (5 failed attempts)

### Attempts Log
- #1: Built dynamic-mask pipeline using ORB homography + Farneback residuals + frame-difference union with CSR export; file/format tests passed, but motion F1 and mask comprehensiveness failed.
- #2: Switched to explicit clip-level motion estimation plus more constrained dynamic masking (pairwise compensated diffs, residual-point support, stabilized MOG2, temporal union); motion accuracy passed, but mask comprehensiveness still failed.
- #3: Reworked masks into dense multi-cue voting with temporal propagation and resized processing for speed; this regressed motion accuracy and still failed mask comprehensiveness.
- #4: Partially restored chain-transform-based mask alignment on resized frames and added broader temporal/MOG unions with heuristics and temporal smoothing; output formats still passed, but motion F1 remained regressed and masks still failed comprehensiveness.
- #5: Tried recall-via-semantic-assisted masks on resized frames (stabilized residuals + MOG2 + compensated diffs + semantic box gating/intersection); both motion F1 and mask comprehensiveness still failed.

### Commands From Last Attempt
- Ran `/root/run_egomotion_dynamic.py`
- Inspected `pred_instructions.json` and `pred_dyn_masks.npz`, including per-frame mask coverage stats
- Generated mask overlay montage from NPZ predictions for visual spot-checking
- Patched `build_dynamic_masks` thresholds/morphology:
  - raised compensated-diff thresholds
  - raised stabilized-residual threshold and reduced dilation
  - reduced cue dilation/closing strength
  - changed semantic box logic to require motion support, then intersected dilated cue with semantic regions
  - lowered connected-component area floor
  - added person-box fallback when filtered mask coverage was tiny
- Re-ran `/root/run_egomotion_dynamic.py`
- Recomputed mask coverage stats and regenerated montage

### Verified Facts
- Output generation and serialization are correct: both required files exist and all structure/format tests pass.
- `pred_instructions.json` as a dict with key `0->20` and valid labels is accepted by format tests.
- `pred_dyn_masks.npz` with `shape` plus per-frame CSR components is accepted by format tests.
- Attempt #2 confirmed the motion-label logic can pass `TestMotionAccuracy::test_motion_macro_f1` when its stronger clip-level path is preserved.
- Dynamic mask accuracy has failed in every attempt so far; mask comprehensiveness is the persistent blocker.
- The resized 480px affine/mask pipeline has not fixed mask recall and may weaken geometric estimation.
- Dense vote fusion with temporal propagation (#3) did not recover mask comprehensiveness and also hurt motion.
- Spatial priors, component rejection, and temporal erosion (#4) did not satisfy mask comprehensiveness.
- Semantic-assisted gating/intersection (#5) also did not satisfy mask comprehensiveness; requiring semantic-motion agreement appears too restrictive and likely suppresses recall.
- Recent motion regressions are likely due to modifications around the formerly working attempt-#2 motion path (shared transforms, resized-frame estimation, threshold tweaks, or coupling with later mask changes), not output formatting.

### Current Error Pattern
Both failing tests remain. Motion labeling is still below the passing attempt-#2 behavior, indicating the motion path has not been faithfully restored. Dynamic masks remain insufficiently comprehensive; the current resized, heuristic-heavy, and now semantic-gated mask construction still misses true dynamic regions rather than broadening recall.

### Next Strategy
Fully fork the solution into two isolated paths. First, restore motion estimation/classification as literally as possible to the attempt-#2 implementation, with no shared resized affines/transforms or threshold edits from later attempts. Second, replace the current semantic-gated mask builder with a simple recall-first full-resolution background-subtraction pipeline independent of motion labels: run MOG2/KNN on original frames, union with raw frame differencing to prev/next frames, optionally union semantic detections directly (not intersected/gated), then apply only minimal cleanup (tiny speck removal). Avoid stabilized warping, chain transforms, spatial priors, semantic-motion intersection, and aggressive morphology. This differs from prior attempts by abandoning compensated/stabilized resized-mask geometry entirely and using a separate full-resolution foreground-segmentation path aimed purely at recall.