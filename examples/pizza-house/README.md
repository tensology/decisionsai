# Ember & Crust Pizza House

A small, runnable project used to exercise DecisionsAI project orchestration against realistic scoped work. It is an example product, not an end-to-end test fixture.

Run it with:

```bash
python3 -m http.server 4173 --directory examples/pizza-house
```

Then open `http://127.0.0.1:4173`.

The project backlog and workflow route policy are in `project-work.json`. Work is intentionally separated into discovery, implementation, validation, and review so different model routes can be used without sharing hidden conversational context.
