import { test } from "node:test";
import assert from "node:assert/strict";

// invalid price — catch zero, negative, non-number, Infinity and NaN.
import { validatePizza } from "../src/menu-validation.mjs";

test("invalid-price catches zero", () => {
  const p = { id: "ember", name: "The Ember", description: "Nduja pizza", price: 0 };
  assert.ok(validatePizza(p).length > 0);
});

test("invalid-price catches negative values", () => {
  const p = { id: "ember", name: "The Ember", description: "Nduja pizza", price: -15 };
  assert.ok(validatePizza(p).length > 0);
});

test("invalid-price catches non-number (String)", () => {
  const p = { id: "ember", name: "The Ember", description: "Nduja pizza", price: "free" };
  // string is not a number — price must be numeric
  const errors = validatePizza(p);
  assert.ok(errors.includes("invalid-price"));
});

test("invalid-price catches Infinity and NaN", () => {
  for (const bad of [Infinity, -Infinity, NaN]) {
    const p = { id: "ember", name: "The Ember", description: "Nduja pizza", price: bad };
    assert.ok(
      validatePizza(p).includes("invalid-price"),
      `expected invalid-price for ${bad}, got ${validatePizza(p)}`
    );
  }
});

test("valid positive integer passes price check", () => {
  const p = { id: "ember", name: "The Ember", description: "Nduja pizza", price: 148 };
  assert.deepEqual(validatePizza(p), []);
});
