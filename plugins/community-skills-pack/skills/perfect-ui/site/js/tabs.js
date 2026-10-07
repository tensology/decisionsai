/* Tabs — In Practice section. Accessible (ARIA + arrow keys). */
(function () {
  'use strict';

  var tabs = document.querySelectorAll('[role="tab"]');
  if (!tabs.length) return;

  function activate(tab) {
    tabs.forEach(function (t) {
      var panel = document.getElementById(t.getAttribute('aria-controls'));
      var isActive = t === tab;
      t.setAttribute('aria-selected', isActive ? 'true' : 'false');
      t.setAttribute('tabindex', isActive ? '0' : '-1');
      if (panel) panel.setAttribute('data-active', isActive ? 'true' : 'false');
    });
    tab.focus();
  }

  tabs.forEach(function (tab, idx) {
    tab.addEventListener('click', function () { activate(tab); });
    tab.addEventListener('keydown', function (e) {
      var key = e.key;
      var next;
      if (key === 'ArrowRight') next = tabs[(idx + 1) % tabs.length];
      else if (key === 'ArrowLeft') next = tabs[(idx - 1 + tabs.length) % tabs.length];
      else if (key === 'Home') next = tabs[0];
      else if (key === 'End') next = tabs[tabs.length - 1];
      if (next) { e.preventDefault(); activate(next); }
    });
  });
})();
