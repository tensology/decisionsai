# Jev Model Router

Use `jev_route_model` when more than one available model could perform the task and the choice materially affects quality, cost, or latency.

## Prepare the decision

1. Describe the task without including unrelated conversation history.
2. List only models the current environment can actually invoke.
3. Describe each model using known capabilities, relative cost, and relative latency. Do not invent benchmarks.
4. Order the decision priorities and include binding constraints.
5. Set stakes by the consequence of a wrong result.

Call `jev_route_model`, then use the selected model if it remains available and allowed. A high `escalate` value means the selected model should be paired with human review or a second independent check.

Routing is advisory. It does not authorize access to a model, increase budget, or weaken the task's existing verification requirements.
