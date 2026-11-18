# Backtest 20252026 (variant full)

| Mode | n | Brier | Log loss | Accuracy |
|---|---|---|---|---|
| none | 1312 | 0.2430 | 0.6788 | 0.541 |
| actual | 1312 | 0.2430 | 0.6788 | 0.541 |
| projected | 1312 | 0.2430 | 0.6788 | 0.541 |
| baseline (home win rate) | 1312 | 0.2511 | 0.6955 | |

Judged on: projected. Gate: PASSED.
Mean lambda per team: 2.86; mean home multiplier: 1.057; goalie multipliers at clamp: 0.0 %.

## Controller override (2026-11-18)
Tuning picked goalie_weight 0.0 (grid, 1000 sims). Re-measured at 2000 sims: goalie_weight 0.6 improves log loss on the tune season 2024-25 (none 0.6656 → projected 0.6643, actual 0.6639) and is neutral on the gate season 2025-26 (none 0.67879, projected 0.6789, actual 0.67888). Set goalie_weight = 0.6 (spec default); gate re-judged on projected at 0.6: Brier 0.24305 < 0.2511, log loss 0.6789 < 0.6955 → PASSED.
