const observedPizzaIds = new Set();

function hasRequiredText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function validatePizzaFields(pizza) {
  const errors = [];
  if (!pizza || typeof pizza !== "object" || Array.isArray(pizza)) {
    return ["invalid-pizza"];
  }
  if (!hasRequiredText(pizza.id)) errors.push("missing-required-id");
  if (!hasRequiredText(pizza.name)) errors.push("missing-required-name");
  if (!hasRequiredText(pizza.description)) errors.push("missing-required-description");
  if (typeof pizza.price !== "number" || !Number.isFinite(pizza.price) || pizza.price <= 0) {
    errors.push("invalid-price");
  }
  return errors;
}

export function validatePizza(pizza) {
  const errors = validatePizzaFields(pizza);
  if (errors.length || !hasRequiredText(pizza.id)) return errors;

  const id = pizza.id.trim();
  if (observedPizzaIds.has(id)) errors.push("duplicate-pizza-id");
  else observedPizzaIds.add(id);
  return errors;
}

export function validateMenu(pizzas) {
  if (!Array.isArray(pizzas)) return ["invalid-menu"];
  const ids = new Set();
  const errors = [];

  pizzas.forEach((pizza, index) => {
    validatePizzaFields(pizza).forEach((code) => errors.push(`${index}:${code}`));
    if (!pizza || !hasRequiredText(pizza.id)) return;
    const id = pizza.id.trim();
    if (ids.has(id)) errors.push(`${index}:duplicate-pizza-id`);
    else ids.add(id);
  });
  return errors;
}
