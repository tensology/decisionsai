/* FAQ — accessible disclosure pattern. Click question to toggle answer. */
(function () {
  'use strict';

  var items = document.querySelectorAll('.faq-item');
  if (!items.length) return;

  items.forEach(function (item) {
    var btn = item.querySelector('.faq-question');
    if (!btn) return;

    btn.addEventListener('click', function () {
      var isOpen = item.getAttribute('data-open') === 'true';
      item.setAttribute('data-open', isOpen ? 'false' : 'true');
      btn.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
    });
  });
})();
