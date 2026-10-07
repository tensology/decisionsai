/* Lenis smooth scroll — Tier 2 motion per references/motion-patterns.md.
   Activated for site at motion intensity 2/3. Guards:
   - DISABLED when prefers-reduced-motion: reduce
   - DISABLED on touch-primary devices (native scroll feels better)
   - SYNCED with GSAP ScrollTrigger via gsap.ticker
   - EXPOSED as window.__lenis for main.js anchor-link routing.

   Editorial-Premium personality config:
   - lerp 0.1 — subtle, not sticky
   - duration 1.2 — slow but not draggy
   - wheelMultiplier 1.0 — natural feel, no acceleration */
(function () {
  'use strict';

  // ---------------------------------------------------------------
  // Guards
  // ---------------------------------------------------------------
  var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) {
    document.documentElement.classList.add('lenis-disabled');
    return;
  }

  var isTouchPrimary = window.matchMedia('(hover: none) and (pointer: coarse)').matches;
  if (isTouchPrimary) {
    document.documentElement.classList.add('lenis-disabled');
    return;
  }

  // Bail if Lenis CDN failed to load
  if (typeof window.Lenis === 'undefined') {
    document.documentElement.classList.add('lenis-disabled');
    return;
  }

  // ---------------------------------------------------------------
  // Init Lenis — editorial-premium config
  // ---------------------------------------------------------------
  var lenis = new window.Lenis({
    lerp: 0.1,
    duration: 1.2,
    smoothWheel: true,
    wheelMultiplier: 1.0,
    touchMultiplier: 2.0,
    syncTouch: false,
    easing: function (t) { return Math.min(1, 1.001 - Math.pow(2, -10 * t)); }
  });

  // Expose for anchor links in main.js
  window.__lenis = lenis;

  // ---------------------------------------------------------------
  // GSAP / ScrollTrigger integration
  // ---------------------------------------------------------------
  if (window.gsap) {
    var gsap = window.gsap;

    // Drive Lenis from GSAP's ticker (single rAF, smoother sync)
    gsap.ticker.add(function (time) {
      lenis.raf(time * 1000);
    });
    gsap.ticker.lagSmoothing(0);

    // Keep ScrollTrigger in sync with Lenis's virtual scroll position
    if (window.ScrollTrigger) {
      lenis.on('scroll', window.ScrollTrigger.update);
    }
  } else {
    // Fallback: standard rAF loop if GSAP isn't present
    function raf(time) {
      lenis.raf(time);
      requestAnimationFrame(raf);
    }
    requestAnimationFrame(raf);
  }

  // ---------------------------------------------------------------
  // Stop Lenis when modals / dropdowns lock body scroll (defensive)
  // ---------------------------------------------------------------
  // Watch for body class `scroll-locked` (not currently used but reserved)
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver(function () {
      if (document.body.classList.contains('scroll-locked')) {
        lenis.stop();
      } else {
        lenis.start();
      }
    }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }
})();
