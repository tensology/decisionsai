import { test } from "node:test";
import assert from "node:assert/strict";

// duplicate id detection - real pizza entries share the same ids across slices, so
// any two pizzas with identical `id` fields should raise a validation failure.
import { validatePizza } from "../src/menu-validation.mjs";

test("duplicate-pizza-id catches duplicate ids", () => {
  const p1 = { id: "ember", name: "A", description: "B", price: 50 };
  const p2 = { id: "ember", name: "C", description: "D", price: 60 };

  const e1 = validatePizza(p1); // first occurrence is fine → no error
  const e2 = validatePizza(p2); // second occurrence with same id → duplicate error

  assert.deepEqual(e1, []);
  assert.ok(e2.length > 0);
  assert.match(String(e2), /duplicate-pizza-id/);
});
