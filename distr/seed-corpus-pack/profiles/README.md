# Boot profiles

Pick one JSON profile and load files in `load_order` (paths relative to pack root).

```
profiles/ecommerce.json      # default for "build an e-commerce website"
profiles/trading-robot.json  # trading / FX robots
```

Agent boot: read profile → follow load_order → then FRAC the feature → scaffold from `scaffold_hints`.
