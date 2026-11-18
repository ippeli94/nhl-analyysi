# Backtest 20252026 (variant full)

| Mode | n | Brier | Log loss | Accuracy |
|---|---|---|---|---|
| none | 1312 | 0.2430 | 0.6789 | 0.542 |
| actual | 1312 | 0.2431 | 0.6790 | 0.554 |
| projected | 1312 | 0.2431 | 0.6790 | 0.550 |
| baseline (home win rate) | 1312 | 0.2511 | 0.6955 | |

Judged on: none. Gate: PASSED.
Mean lambda per team: 2.86; mean home multiplier: 1.057; goalie multipliers at clamp: 0.0 %.

## Draw calibration

Fitted kappa 1.610.

| Season | n | Model | Actual | Gap | 1X2 log loss (k=1) | 1X2 log loss (fitted) |
|---|---|---|---|---|---|---|
| 20242025 | 1312 | 0.2473 | 0.2066 | +0.0408 | 1.0362 | 1.0363 |
| 20252026 | 1312 | 0.2447 | 0.2485 | -0.0038 | 1.0838 | 1.0628 |
| 20262027 | 332 | 0.2380 | 0.2229 | +0.0151 | 1.0442 | 1.0326 |

## Controller override (2026-11-18, after draw refit)
The refit judged goalie mode "none" because projected log loss was 0.0001 worse (0.6790 vs 0.6789), which is within simulation noise. Kept use_goalie = true with goalie_weight 0.6, as in the phase-1 override (on 2024-25 the goalie factor improved log loss from 0.6656 to 0.6643). Gate re-judged on projected: Brier 0.2431 < 0.2511, log loss 0.6790 < 0.6955, so it PASSED.
