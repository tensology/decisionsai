const pizzas=[
  {name:"The Ember",description:"Nduja, smoked mozzarella, hot honey, basil",price:"R148"},
  {name:"Market Green",description:"Zucchini, lemon ricotta, herbs, pecorino",price:"R136"},
  {name:"Karoo Mushroom",description:"Roast mushroom, thyme, garlic cream, fontina",price:"R142"}
];
document.querySelector("#pizza-grid").innerHTML=pizzas.map(pizza=>`<article class="card"><h3>${pizza.name}</h3><p>${pizza.description}</p><span class="price">${pizza.price}</span></article>`).join("");
