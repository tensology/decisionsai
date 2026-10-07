/* GSAP animations — Brand Motion Identity: Editorial-Premium personality.
   - Signature easing: power3.out (cubic-bezier 0.16, 1, 0.3, 1 equivalent)
   - Duration palette: quick 0.4 / standard 0.9 / slow 1.4
   - Entrance pattern: masked line reveal + subtle upward translate + opacity fade
   Respects prefers-reduced-motion. */
(function () {
  'use strict';

  // Bail out if GSAP failed to load
  if (typeof window.gsap === 'undefined') {
    document.documentElement.classList.add('no-js');
    return;
  }

  var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) return;

  var gsap = window.gsap;
  if (window.ScrollTrigger) gsap.registerPlugin(window.ScrollTrigger);

  // Brand defaults
  gsap.defaults({
    ease: 'power3.out',
    duration: 0.9
  });

  // ---------------------------------------------------------------
  // HERO — masked line reveal + sub fade + stats stagger
  // ---------------------------------------------------------------
  var heroHead = document.querySelector('.hero-head');
  var heroLines = document.querySelectorAll('.h1 .line-inner');
  var heroSub = document.querySelector('.hero-sub');
  var heroStats = document.querySelectorAll('.hero-stats > *');

  var tl = gsap.timeline();

  if (heroHead) {
    tl.to(heroHead, { opacity: 1, duration: 0.6 }, 0);
  }

  if (heroLines.length) {
    tl.to(heroLines, {
      y: '0%',
      duration: 1.2,
      stagger: 0.12,
      ease: 'power4.out'
    }, 0.15);
  }

  if (heroSub) {
    tl.to(heroSub, {
      y: 0,
      opacity: 1,
      duration: 0.9
    }, 0.7);
  }

  if (heroStats.length) {
    tl.to(heroStats, {
      y: 0,
      opacity: 1,
      duration: 0.7,
      stagger: 0.08
    }, 0.9);
  }

  // ---------------------------------------------------------------
  // COUNTERS — animate target numbers in stats blocks
  // ---------------------------------------------------------------
  document.querySelectorAll('.counter[data-target]').forEach(function (el) {
    var target = parseInt(el.getAttribute('data-target'), 10);
    if (isNaN(target)) return;

    var obj = { val: 0 };
    var trigger = window.ScrollTrigger
      ? { scrollTrigger: { trigger: el, start: 'top 85%', once: true } }
      : {};

    gsap.to(obj, Object.assign({
      val: target,
      duration: 1.4,
      ease: 'power2.out',
      onUpdate: function () {
        el.textContent = Math.round(obj.val);
      }
    }, trigger));
  });

  // ---------------------------------------------------------------
  // SCROLL-TRIGGERED REVEALS — single-element + stagger groups
  // ---------------------------------------------------------------
  if (window.ScrollTrigger) {

    // Single .reveal elements
    document.querySelectorAll('.reveal').forEach(function (el) {
      gsap.to(el, {
        y: 0,
        opacity: 1,
        duration: 0.9,
        scrollTrigger: {
          trigger: el,
          start: 'top 88%',
          once: true
        }
      });
    });

    // .reveal-soft (smaller move)
    document.querySelectorAll('.reveal-soft').forEach(function (el) {
      gsap.to(el, {
        y: 0,
        opacity: 1,
        duration: 0.7,
        scrollTrigger: {
          trigger: el,
          start: 'top 90%',
          once: true
        }
      });
    });

    // .reveal-fade (opacity only)
    document.querySelectorAll('.reveal-fade').forEach(function (el) {
      gsap.to(el, {
        opacity: 1,
        duration: 0.8,
        scrollTrigger: {
          trigger: el,
          start: 'top 90%',
          once: true
        }
      });
    });

    // Stagger groups — animate children
    document.querySelectorAll('.stagger').forEach(function (group) {
      // Skip hero-stats — handled by hero timeline
      if (group.classList.contains('hero-stats')) return;

      var children = group.children;
      if (!children.length) return;

      gsap.to(children, {
        y: 0,
        opacity: 1,
        duration: 0.7,
        stagger: 0.08,
        scrollTrigger: {
          trigger: group,
          start: 'top 85%',
          once: true
        }
      });
    });

    // Section labels — small fade-in from left
    gsap.utils.toArray('.sec-head').forEach(function (head) {
      var label = head.querySelector('.label');
      var heading = head.querySelector('h2');
      var meta = head.querySelector('.sec-meta');
      var elems = [label, heading, meta].filter(Boolean);

      gsap.from(elems, {
        y: 14,
        opacity: 0,
        duration: 0.7,
        stagger: 0.06,
        scrollTrigger: {
          trigger: head,
          start: 'top 88%',
          once: true
        }
      });
    });
  }

  // ---------------------------------------------------------------
  // CURSOR ACCENT — premium personality micro-interaction
  // ---------------------------------------------------------------
  var cursor = document.querySelector('.cursor-accent');
  if (cursor && window.matchMedia('(pointer: fine)').matches) {
    var hasMoved = false;
    var x = -100, y = -100;
    var tx = -100, ty = -100;

    document.addEventListener('mousemove', function (e) {
      if (!hasMoved) {
        cursor.classList.add('active');
        hasMoved = true;
      }
      tx = e.clientX;
      ty = e.clientY;
    });

    gsap.ticker.add(function () {
      x += (tx - x) * 0.18;
      y += (ty - y) * 0.18;
      cursor.style.transform = 'translate3d(' + (x - 4) + 'px,' + (y - 4) + 'px,0)';
    });

    // Grow cursor near interactive elements
    document.querySelectorAll('a, button, .phase, .mandate, .tier').forEach(function (el) {
      el.addEventListener('mouseenter', function () {
        gsap.to(cursor, { scale: 3, duration: 0.3, opacity: 0.35 });
      });
      el.addEventListener('mouseleave', function () {
        gsap.to(cursor, { scale: 1, duration: 0.3, opacity: 0.7 });
      });
    });
  }
})();
