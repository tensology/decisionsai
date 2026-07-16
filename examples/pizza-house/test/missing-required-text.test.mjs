import { test } from "node:test";
import assert from "node:assert/strict";

// missing required text — blank or whitespace-only name / description must fail.
import { validatePizza } from "../src/menu-validation.mjs";

const commonBase = { id: "ember" };

test("missing-required-name catches absent name", () => {
  const p = { ...commonBase, price: 150, name: undefined, description: "Nduja" };
  assert.ok(validatePizza(p).includes("missing-required-name"));
});

test("missing-required-description catches absent description", () => {
  const p = { ...commonBase, name: "The Ember", description: undefined, price: 150 };
  assert.ok(validatePizza(p).includes("missing-required-description"));
});

test("missing-required-name catches empty string name", () => {
  const p = { ...commonBase, price: 150, name: "", description: "Nduja" };
  assert.ok(validatePizza(p).includes("missing-required-name"));
});

test("missing-required-description catches whitespace-only description", () => {
  const p = { ...commonBase, name: "The Ember", price: 150, description: "   " };
  assert.ok(validatePizza(p).includes("missing-required-description"));
});

test("valid pizza with all fields passes", () => {
  const p = { id: "ember", name: "The Ember", description: "Nduja pizza", price: 148 };
  assert.deepEqual(validatePizza(p), []);
});
