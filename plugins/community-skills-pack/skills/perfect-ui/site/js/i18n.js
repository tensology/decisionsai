/* i18n — language detection + JSON load + DOM swap + language switcher.
   Languages: en (baseline), vi, zh, ko, ja, es.
   Detection priority: ?lang URL param → localStorage → navigator.language → 'en'.
   Glossary lock: code blocks, file paths, vibe table, font names stay English (handled at translation source). */
(function () {
  'use strict';

  var SUPPORTED = ['en', 'vi', 'zh', 'ko', 'ja', 'es'];
  var DEFAULT_LANG = 'en';
  var STORAGE_KEY = 'perfectUiLang';
  var DICTS = {}; // cache loaded dictionaries

  // ---------------------------------------------------------------
  // Detect initial language
  // ---------------------------------------------------------------
  function detectLang() {
    var params = new URLSearchParams(window.location.search);
    var urlLang = params.get('lang');
    if (urlLang && SUPPORTED.indexOf(urlLang) !== -1) return urlLang;

    var stored = null;
    try { stored = localStorage.getItem(STORAGE_KEY); } catch (e) {}
    if (stored && SUPPORTED.indexOf(stored) !== -1) return stored;

    var nav = (navigator.language || navigator.userLanguage || '').toLowerCase();
    if (nav) {
      for (var i = 0; i < SUPPORTED.length; i++) {
        if (nav.indexOf(SUPPORTED[i]) === 0) return SUPPORTED[i];
      }
      // zh-CN, zh-TW etc all map to 'zh'
      if (nav.indexOf('zh') === 0) return 'zh';
    }

    return DEFAULT_LANG;
  }

  // ---------------------------------------------------------------
  // Load dictionary JSON
  // ---------------------------------------------------------------
  function loadDict(lang) {
    if (DICTS[lang]) return Promise.resolve(DICTS[lang]);
    return fetch('i18n/' + lang + '.json')
      .then(function (r) {
        if (!r.ok) throw new Error('Failed to load ' + lang + '.json');
        return r.json();
      })
      .then(function (data) { DICTS[lang] = data; return data; });
  }

  // ---------------------------------------------------------------
  // Resolve dotted key path from dict
  // ---------------------------------------------------------------
  function lookup(dict, path) {
    var parts = path.split('.');
    var cur = dict;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== 'object') return null;
      cur = cur[parts[i]];
    }
    return (typeof cur === 'string') ? cur : null;
  }

  // ---------------------------------------------------------------
  // Apply dictionary to DOM
  // ---------------------------------------------------------------
  function apply(dict) {
    // data-i18n: textContent swap
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      var val = lookup(dict, key);
      if (val == null) return;
      el.textContent = val;
    });

    // data-i18n-html: innerHTML swap (allows <em>, <strong>, <code>)
    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-html');
      var val = lookup(dict, key);
      if (val == null) return;
      el.innerHTML = val;
    });

    // data-i18n-attr-{attrName}: attribute swap (e.g. aria-label, title, placeholder)
    document.querySelectorAll('*').forEach(function (el) {
      for (var i = 0; i < el.attributes.length; i++) {
        var name = el.attributes[i].name;
        if (name.indexOf('data-i18n-attr-') === 0) {
          var attr = name.substring('data-i18n-attr-'.length);
          var key = el.getAttribute(name);
          var val = lookup(dict, key);
          if (val != null) el.setAttribute(attr, val);
        }
      }
    });

    // Update <html lang> + <body class>
    document.documentElement.setAttribute('lang', dict._meta.lang);
    document.documentElement.setAttribute('dir', dict._meta.dir || 'ltr');
    document.body.classList.remove('i18n-loading');
    document.body.classList.remove('lang-en', 'lang-vi', 'lang-zh', 'lang-ko', 'lang-ja', 'lang-es');
    document.body.classList.add('lang-' + dict._meta.lang);
  }

  // ---------------------------------------------------------------
  // Switch language (called from switcher UI)
  // ---------------------------------------------------------------
  function switchTo(lang) {
    if (SUPPORTED.indexOf(lang) === -1) return;

    document.body.classList.add('i18n-switching');

    loadDict(lang).then(function (dict) {
      apply(dict);
      try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) {}

      // Update URL without reload
      var url = new URL(window.location.href);
      if (lang === DEFAULT_LANG) {
        url.searchParams.delete('lang');
      } else {
        url.searchParams.set('lang', lang);
      }
      window.history.replaceState({}, '', url.toString());

      // Update switcher UI state
      updateSwitcherState(lang);

      // Re-trigger animation reveals for changed text (skip — content already visible after first load)
      document.body.classList.remove('i18n-switching');
    }).catch(function (err) {
      console.warn('[i18n]', err);
      document.body.classList.remove('i18n-switching');
    });
  }

  // ---------------------------------------------------------------
  // Build + wire up language switcher in nav
  // ---------------------------------------------------------------
  function buildSwitcher(currentLang) {
    var mount = document.querySelector('[data-lang-switcher]');
    if (!mount) return;

    var btn = mount.querySelector('.lang-trigger');
    var menu = mount.querySelector('.lang-menu');
    if (!btn || !menu) return;

    // Toggle open/close
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = mount.getAttribute('data-open') === 'true';
      mount.setAttribute('data-open', isOpen ? 'false' : 'true');
      btn.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
    });

    // Click outside to close
    document.addEventListener('click', function () {
      mount.setAttribute('data-open', 'false');
      btn.setAttribute('aria-expanded', 'false');
    });
    menu.addEventListener('click', function (e) { e.stopPropagation(); });

    // Item click → switch
    menu.querySelectorAll('[data-lang-option]').forEach(function (opt) {
      opt.addEventListener('click', function () {
        var lang = opt.getAttribute('data-lang-option');
        switchTo(lang);
        mount.setAttribute('data-open', 'false');
        btn.setAttribute('aria-expanded', 'false');
        btn.focus();
      });

      // Keyboard nav within menu
      opt.addEventListener('keydown', function (e) {
        var items = menu.querySelectorAll('[data-lang-option]');
        var idx = Array.prototype.indexOf.call(items, opt);
        var next;
        if (e.key === 'ArrowDown') next = items[(idx + 1) % items.length];
        else if (e.key === 'ArrowUp') next = items[(idx - 1 + items.length) % items.length];
        else if (e.key === 'Home') next = items[0];
        else if (e.key === 'End') next = items[items.length - 1];
        else if (e.key === 'Escape') {
          e.preventDefault();
          mount.setAttribute('data-open', 'false');
          btn.setAttribute('aria-expanded', 'false');
          btn.focus();
          return;
        }
        if (next) { e.preventDefault(); next.focus(); }
      });
    });

    // Open menu via keyboard on trigger
    btn.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        mount.setAttribute('data-open', 'true');
        btn.setAttribute('aria-expanded', 'true');
        var first = menu.querySelector('[data-lang-option]');
        if (first) first.focus();
      }
    });

    updateSwitcherState(currentLang);
  }

  function updateSwitcherState(lang) {
    var mount = document.querySelector('[data-lang-switcher]');
    if (!mount) return;

    var code = mount.querySelector('.lang-trigger-code');
    if (code) code.textContent = (DICTS[lang] && DICTS[lang]._meta.code) || lang.toUpperCase();

    mount.querySelectorAll('[data-lang-option]').forEach(function (opt) {
      var isCurrent = opt.getAttribute('data-lang-option') === lang;
      opt.setAttribute('aria-selected', isCurrent ? 'true' : 'false');
      opt.setAttribute('data-current', isCurrent ? 'true' : 'false');
    });
  }

  // ---------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------
  document.body.classList.add('i18n-loading');

  var initialLang = detectLang();

  // Preload all dictionaries for fast switching (~5KB each, fine on HTTP/2)
  var preloads = SUPPORTED.map(function (l) { return loadDict(l).catch(function () { return null; }); });

  Promise.all(preloads).then(function () {
    var dict = DICTS[initialLang] || DICTS[DEFAULT_LANG];
    if (!dict) {
      // No JSON loaded — keep EN default content visible
      document.body.classList.remove('i18n-loading');
      return;
    }
    apply(dict);
    buildSwitcher(initialLang);
  });
})();
