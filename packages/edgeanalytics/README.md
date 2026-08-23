# retailsense-edgeanalytics

Per-camera analytics that turn tracker output into privacy-safe aggregates:
zone membership (2-frame inertia), line crossings (normative side rule with
segment check, 2 frames per side and 2 s cooldown), dwell samples, occupancy
cadence, a floor-space heatmap (deltas) and a footfall counter with 15-minute
spike detection.  Depends only on `retailsense_contracts`.
