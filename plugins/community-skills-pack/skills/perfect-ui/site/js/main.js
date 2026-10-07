/* Main — small glue: copy-to-clipboard for install commands + smooth-anchor offset */
(function () {
  'use strict';

  // ---------------------------------------------------------------
  // COPY TO CLIPBOARD — install command boxes
  // ---------------------------------------------------------------
  document.querySelectorAll('.copy-btn[data-copy-target]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var targetId = btn.getAttribute('data-copy-target');
      var target = document.getElementById(targetId);
      if (!target) return;

      var text = target.textContent.trim();

      var done = function () {
        var original = btn.textContent;
        btn.textContent = 'Copied';
        btn.setAttribute('data-copied', 'true');
        setTimeout(function () {
          btn.textContent = original;
          btn.removeAttribute('data-copied');
        }, 1800);
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); });
      } else {
        fallbackCopy(text, done);
      }
    });
  });

  function fallbackCopy(text, done) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    document.body.removeChild(ta);
  }

  // ---------------------------------------------------------------
  // NAV — anchor offset so sticky header doesn't cover target.
  // Routes through Lenis when available (smoother), falls back to
  // native window.scrollTo otherwise.
  // ---------------------------------------------------------------
  var NAV_OFFSET = 60 + 12;

  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener('click', function (e) {
      var hash = link.getAttribute('href');
      if (hash === '#' || hash.length < 2) return;
      var target = document.querySelector(hash);
      if (!target) return;
      e.preventDefault();

      if (window.__lenis && typeof window.__lenis.scrollTo === 'function') {
        // Lenis handles offset internally via the offset option
        window.__lenis.scrollTo(target, { offset: -NAV_OFFSET, duration: 1.4 });
      } else {
        var rect = target.getBoundingClientRect();
        var y = window.pageYOffset + rect.top - NAV_OFFSET;
        window.scrollTo({ top: y, behavior: 'smooth' });
      }
    });
  });
})();
