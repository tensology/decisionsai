# Decision heuristics card — SYNTHESIZED

```
IF request_sharpness >= 4:
  questions = 0..1
  load profile defaults
  expand entity graph
  scaffold + FRAC risky bits only
ELIF request_sharpness <= 1:
  questions = 4..7 (architecture-changing only)
  THEN scaffold
ELSE:
  questions = 2..4 for gaps
  infer remainder from pack

IF decision.reverse_cost == high AND confidence < high:
  ASK with named default
ELSE:
  INFER and proceed

NEVER:
  - stall on font/spacing when stencil+taste gate already decide
  - claim “done” without verify evidence
  - invent payment/inventory topology silently
```
