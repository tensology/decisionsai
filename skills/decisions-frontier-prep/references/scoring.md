# Frontier Queue Scoring

Score packets with this shape:

```text
score = 0.5 * qualify + 0.3 * value + 0.2 * risk
qualify = 0.5 * normalized(rounds_saved) + 0.5 * ceiling_lift
```

Weights:

- `low = 0.34`
- `med = 0.67`
- `high = 1.0`
- `rounds_saved` normalizes to `min(rounds_saved / 5, 1.0)`

Drop packets with `qualify < 0.15`. They belong on normal board tickets.

Wave guidance:

- Wave 1: score >= 0.80, high value or risk, low dependency.
- Wave 2: score >= 0.70, valuable but less urgent or dependent.
- Wave 3: remaining qualified hardening, resilience, and cleanup.
