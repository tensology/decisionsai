import { validateMenu } from "./src/menu-validation.mjs";

const pizzas = [
  {
    id: "ember",
    name: "The Ember",
    tag: "wood-fired",
    description: "Nduja, smoked mozzarella, hot honey, basil",
    price: 148,
    imageAlt: "The Ember pizza with blistered crust and basil",
    image: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 640 420'%3E%3Crect width='640' height='420' fill='%23201c18'/%3E%3Ccircle cx='332' cy='214' r='150' fill='%23f1c36b'/%3E%3Ccircle cx='332' cy='214' r='128' fill='%23d64b2a'/%3E%3Ccircle cx='270' cy='168' r='26' fill='%23762516'/%3E%3Ccircle cx='395' cy='232' r='28' fill='%23762516'/%3E%3Ccircle cx='322' cy='282' r='22' fill='%23762516'/%3E%3Cpath d='M210 260c95-32 182-32 265 0' stroke='%23fffaf0' stroke-width='16' stroke-linecap='round' fill='none'/%3E%3C/svg%3E"
  },
  {
    id: "market-green",
    name: "Market Green",
    tag: "garden",
    description: "Zucchini, lemon ricotta, herbs, pecorino",
    price: 136,
    imageAlt: "Market Green pizza with herbs and lemon ricotta",
    image: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 640 420'%3E%3Crect width='640' height='420' fill='%23344731'/%3E%3Ccircle cx='318' cy='214' r='154' fill='%23e5c983'/%3E%3Ccircle cx='318' cy='214' r='130' fill='%23fffaf0'/%3E%3Cellipse cx='252' cy='196' rx='20' ry='48' fill='%235d7c41' transform='rotate(-32 252 196)'/%3E%3Cellipse cx='367' cy='162' rx='18' ry='44' fill='%235d7c41' transform='rotate(34 367 162)'/%3E%3Cellipse cx='392' cy='278' rx='20' ry='48' fill='%235d7c41' transform='rotate(-18 392 278)'/%3E%3Ccircle cx='302' cy='272' r='24' fill='%23f0d889'/%3E%3Ccircle cx='420' cy='220' r='20' fill='%23f0d889'/%3E%3C/svg%3E"
  },
  {
    id: "karoo-mushroom",
    name: "Karoo Mushroom",
    tag: "earthy",
    description: "Roast mushroom, thyme, garlic cream, fontina",
    price: 142,
    imageAlt: "Karoo Mushroom pizza with roasted mushrooms",
    image: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 640 420'%3E%3Crect width='640' height='420' fill='%237a6958'/%3E%3Ccircle cx='320' cy='214' r='154' fill='%23dfb66f'/%3E%3Ccircle cx='320' cy='214' r='130' fill='%23f4efe5'/%3E%3Cellipse cx='260' cy='184' rx='46' ry='24' fill='%23805f45' transform='rotate(-20 260 184)'/%3E%3Cellipse cx='382' cy='248' rx='50' ry='25' fill='%23805f45' transform='rotate(28 382 248)'/%3E%3Cellipse cx='326' cy='300' rx='42' ry='22' fill='%23805f45' transform='rotate(-7 326 300)'/%3E%3Cpath d='M232 252c58-28 123-32 195-10' stroke='%23d64b2a' stroke-width='14' stroke-linecap='round' fill='none'/%3E%3C/svg%3E"
  }
];

const menuErrors = validateMenu(pizzas);
if (menuErrors.length) {
  throw new Error(`Invalid pizza menu: ${menuErrors.join(", ")}`);
}

const cart = Object.fromEntries(pizzas.map((pizza) => [pizza.id, 0]));

function formatPrice(amount) {
  return `R${amount}`;
}

function getCartTotal() {
  return pizzas.reduce((sum, pizza) => sum + pizza.price * cart[pizza.id], 0);
}

function renderMenu() {
  const grid = document.querySelector("#pizza-grid");
  grid.innerHTML = pizzas.map((pizza) => `
    <article class="card pizza-card" role="listitem">
      <img src="${pizza.image}" alt="${pizza.imageAlt}" loading="lazy" width="640" height="420">
      <div class="card-body">
        <span class="pill">${pizza.tag}</span>
        <h3>${pizza.name}</h3>
        <p>${pizza.description}</p>
        <div class="card-actions">
          <span class="price">${formatPrice(pizza.price)}</span>
          <button class="button card-button" type="button" data-add="${pizza.id}">Add to order</button>
        </div>
      </div>
    </article>
  `).join("");
}

function renderCart() {
  const cartLines = document.querySelector("#cart-lines");
  cartLines.innerHTML = pizzas.map((pizza) => `
    <div class="cart-line">
      <div>
        <strong>${pizza.name}</strong>
        <span>${formatPrice(pizza.price)} each</span>
      </div>
      <div class="quantity-control" aria-label="${pizza.name} quantity">
        <button type="button" data-decrement="${pizza.id}" aria-label="Remove one ${pizza.name}">-</button>
        <output aria-live="polite">${cart[pizza.id]}</output>
        <button type="button" data-increment="${pizza.id}" aria-label="Add one ${pizza.name}">+</button>
      </div>
    </div>
  `).join("");

  document.querySelector("#subtotal").textContent = formatPrice(getCartTotal());
}

function updateQuantity(id, change) {
  cart[id] = Math.max(0, cart[id] + change);
  renderCart();
}

function bindInteractions() {
  const navToggle = document.querySelector(".nav-toggle");
  const primaryNav = document.querySelector("#primary-nav");
  const checkoutForm = document.querySelector("#checkout-form");

  navToggle.addEventListener("click", () => {
    const isOpen = primaryNav.classList.toggle("nav-open");
    navToggle.setAttribute("aria-expanded", String(isOpen));
  });

  primaryNav.addEventListener("click", (event) => {
    if (event.target.matches("a")) {
      primaryNav.classList.remove("nav-open");
      navToggle.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("click", (event) => {
    const addId = event.target.dataset.add;
    const incrementId = event.target.dataset.increment;
    const decrementId = event.target.dataset.decrement;

    if (addId) {
      updateQuantity(addId, 1);
      document.querySelector("#order").scrollIntoView({ behavior: "smooth", block: "start" });
    }

    if (incrementId) {
      updateQuantity(incrementId, 1);
    }

    if (decrementId) {
      updateQuantity(decrementId, -1);
    }
  });

  checkoutForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const feedback = document.querySelector("#order-feedback");

    if (!checkoutForm.reportValidity()) {
      return;
    }

    if (getCartTotal() === 0) {
      feedback.textContent = "Add at least one pizza before sending the order.";
      return;
    }

    const formData = new FormData(checkoutForm);
    const orderLines = pizzas
      .filter((pizza) => cart[pizza.id] > 0)
      .map((pizza) => `${cart[pizza.id]} x ${pizza.name} (${formatPrice(pizza.price * cart[pizza.id])})`)
      .join("%0A");
    const subject = encodeURIComponent("Ember & Crust order request");
    const body = [
      `Name: ${formData.get("customerName")}`,
      `Email: ${formData.get("customerEmail")}`,
      `Order type: ${formData.get("orderType")}`,
      `Notes: ${formData.get("customerNotes") || "None"}`,
      "",
      "Order:",
      decodeURIComponent(orderLines),
      "",
      `Subtotal: ${formatPrice(getCartTotal())}`
    ].map(encodeURIComponent).join("%0A");

    feedback.textContent = "Opening your email client with the order summary.";
    window.location.href = `mailto:hello@emberandcrust.co.za?subject=${subject}&body=${body}`;
  });
}

document.addEventListener("DOMContentLoaded", () => {
  renderMenu();
  renderCart();
  bindInteractions();
});
