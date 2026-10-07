/* Theme switcher — 11 vibe anchor themes per references/visual-direction-guide.md.
   Detect: ?theme=X → localStorage.perfectUiTheme → fallback 'editorial'.
   Swap: sets <html data-theme="X">. CSS custom-property cascade does the rest.
   Fonts: lazy-load when popover opens (preload once, cached).
   Nav P-logo: 11 SVG variants reflecting per-vibe stroke / corner / proportion.
   Persistence: URL ?theme= + localStorage. */
(function () {
  'use strict';

  var DEFAULT_THEME = 'editorial';
  var STORAGE_KEY = 'perfectUiTheme';

  // ---------------------------------------------------------------
  // Theme catalog — palette swatches for popover tiles + Google Fonts URL
  // ---------------------------------------------------------------
  var THEMES = [
    { id: 'minimal',     num: '01', code: 'MIN', name: 'Minimal',          bg: '#FAFAF7', muted: '#F1F0EB', ink: '#181715', accent: '#2A4A3E', fonts: 'Hanken+Grotesk:wght@400;500;700' },
    { id: 'editorial',   num: '02', code: 'EDT', name: 'Editorial',        bg: '#F5F1E8', muted: '#EDE6D6', ink: '#1A1715', accent: '#B8635A', fonts: null /* Fraunces + Geist loaded in HTML head */ },
    { id: 'brutalist',   num: '03', code: 'BRU', name: 'Brutalist',        bg: '#E5E5E5', muted: '#F5F5F5', ink: '#000000', accent: '#FF3B00', fonts: 'Archivo:ital,wght@0,400;0,500;0,700;0,900;1,400;1,700;1,900' /* drop Archivo Black, use Archivo @ 900 for Vietnamese subset + true italic */ },
    { id: 'retro',       num: '04', code: 'RET', name: 'Retro-futuristic', bg: '#0A0E27', muted: '#1A1F3A', ink: '#E8E6F0', accent: '#FF6B9D', fonts: 'Chakra+Petch:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600|JetBrains+Mono:wght@400;500' },
    { id: 'organic',     num: '05', code: 'ORG', name: 'Organic',          bg: '#F4EFE6', muted: '#E8DCC4', ink: '#2C2418', accent: '#5C7A4A', fonts: 'Lora:wght@400;500;600' /* Fraunces from HTML head */ },
    { id: 'luxury',      num: '06', code: 'LUX', name: 'Luxury',           bg: '#0E0E0C', muted: '#1A1A17', ink: '#F0EBE0', accent: '#C9A961', fonts: 'Cormorant+Garamond:wght@400;500;600|Cormorant:wght@400;500;600' },
    { id: 'playful',     num: '07', code: 'PLY', name: 'Playful',          bg: '#FFF8E7', muted: '#FFE8C2', ink: '#2D1F12', accent: '#FF6B4A', fonts: 'Plus+Jakarta+Sans:wght@400;500;700' /* Fraunces from HTML head */ },
    { id: 'industrial',  num: '08', code: 'IND', name: 'Industrial',       bg: '#1F1E1B', muted: '#2D2C28', ink: '#E5E2DC', accent: '#D45A1A', fonts: 'Archivo:wght@400;500;700|JetBrains+Mono:wght@400;500' },
    { id: 'artdeco',     num: '09', code: 'ART', name: 'Art-deco',         bg: '#1A1614', muted: '#2A211C', ink: '#E8DCC4', accent: '#C9A961', fonts: 'Playfair+Display:wght@400;500;700|Cormorant:wght@400;500;600' },
    { id: 'glass',       num: '10', code: 'GLA', name: 'Glass-tech',       bg: '#0A0F1F', muted: '#141B33', ink: '#E8EAF0', accent: '#5EE7DF', fonts: 'Sora:wght@400;500;700|JetBrains+Mono:wght@400;500' },
    { id: 'handcrafted', num: '11', code: 'HND', name: 'Hand-crafted',     bg: '#F4ECD8', muted: '#E5D6B5', ink: '#2C1F0F', accent: '#8B4A2A', fonts: 'Spectral:wght@400;500;600' /* Fraunces from HTML head */ }
  ];

  // ---------------------------------------------------------------
  // 11 P-logo SVG variants — one per vibe.
  // Same 32×32 viewBox, P glyph + decorative frame, vibe-coded stroke/style.
  // Use currentColor so they inherit ink color of the active theme.
  // ---------------------------------------------------------------
  var LOGOS = {
    minimal:     '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><rect x="2" y="2" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1"/><path d="M11 23V9h5.5c2.4 0 4.1 1.6 4.1 4 0 2.4-1.7 4-4.1 4H13V23h-2zm2-9h3.4c1.2 0 2-.8 2-2s-.8-2-2-2H13v4z" fill="currentColor"/></svg>',
    editorial:   '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><rect x="0.5" y="0.5" width="31" height="31" rx="5.5" fill="none" stroke="currentColor"/><path d="M10 23V9h6c2.7 0 4.6 1.8 4.6 4.4 0 2.6-1.9 4.4-4.6 4.4h-3.2V23H10zm2.8-8.5h3c1.2 0 2-.8 2-2s-.8-2-2-2h-3v4z" fill="currentColor"/></svg>',
    brutalist:   '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.5"/><path d="M9 24V8h7.5c3 0 5.1 2 5.1 4.9 0 3-2.1 5-5.1 5H12V24H9zm3-9.4h4.2c1.4 0 2.4-1 2.4-2.5s-1-2.5-2.4-2.5H12v5z" fill="currentColor"/></svg>',
    retro:       '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><rect x="2" y="2" width="2" height="2" fill="currentColor"/><rect x="28" y="2" width="2" height="2" fill="currentColor"/><rect x="2" y="28" width="2" height="2" fill="currentColor"/><rect x="28" y="28" width="2" height="2" fill="currentColor"/><rect x="4" y="2" width="24" height="2" fill="currentColor"/><rect x="4" y="28" width="24" height="2" fill="currentColor"/><rect x="2" y="4" width="2" height="24" fill="currentColor"/><rect x="28" y="4" width="2" height="24" fill="currentColor"/><path d="M11 23V9h5c2.5 0 4.3 1.8 4.3 4.3 0 2.5-1.8 4.3-4.3 4.3h-2.7V23H11zm2.3-8.6h2.5c1.1 0 1.9-.8 1.9-1.9s-.8-1.9-1.9-1.9h-2.5v3.8z" fill="currentColor"/></svg>',
    organic:     '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><path d="M3 5C3 3 5 2 7 3C12 5 20 5 25 3C27 2 29 3 29 5C30 11 30 21 29 27C29 29 27 30 25 29C20 27 12 27 7 29C5 30 3 29 3 27C2 21 2 11 3 5Z" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10 23V9h6.2c2.7 0 4.5 1.7 4.5 4.2 0 2.5-1.8 4.2-4.5 4.2H13V23h-3zm3-8.7h3c1.1 0 1.9-.7 1.9-1.9s-.8-1.9-1.9-1.9h-3v3.8z" fill="currentColor"/></svg>',
    luxury:      '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><rect x="2.5" y="2.5" width="27" height="27" fill="none" stroke="currentColor" stroke-width="0.6"/><rect x="4.5" y="4.5" width="23" height="23" fill="none" stroke="currentColor" stroke-width="0.4"/><path d="M11.5 23.5V9.5h5c2.5 0 4.2 1.7 4.2 4.1 0 2.4-1.7 4.1-4.2 4.1h-2.6V23.5h-2.4zm2.4-8.6h2.4c1.1 0 1.8-.7 1.8-1.8s-.7-1.8-1.8-1.8h-2.4v3.6z" fill="currentColor"/></svg>',
    playful:     '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><g transform="rotate(-4 16 16)"><rect x="2" y="2" width="28" height="28" rx="8" fill="none" stroke="currentColor" stroke-width="2.5"/><path d="M10 23V9h6.5c2.9 0 4.9 1.9 4.9 4.6 0 2.7-2 4.6-4.9 4.6h-3.5V23H10zm3-8.5h3.2c1.3 0 2.1-.8 2.1-2s-.8-2-2.1-2H13v4z" fill="currentColor"/></g></svg>',
    industrial:  '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><rect x="1.5" y="1.5" width="29" height="29" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="6" y1="1.5" x2="6" y2="4" stroke="currentColor" stroke-width="1"/><line x1="26" y1="1.5" x2="26" y2="4" stroke="currentColor" stroke-width="1"/><line x1="6" y1="28" x2="6" y2="30.5" stroke="currentColor" stroke-width="1"/><line x1="26" y1="28" x2="26" y2="30.5" stroke="currentColor" stroke-width="1"/><path d="M10 23V9h6.3c2.7 0 4.6 1.8 4.6 4.4 0 2.6-1.9 4.4-4.6 4.4h-3.4V23H10zm2.9-8.6h3.2c1.2 0 2-.8 2-2s-.8-2-2-2h-3.2v4z" fill="currentColor"/></svg>',
    artdeco:     '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><g stroke="currentColor" stroke-width="0.8" fill="none"><line x1="16" y1="2" x2="16" y2="6"/><line x1="16" y1="26" x2="16" y2="30"/><line x1="2" y1="16" x2="6" y2="16"/><line x1="26" y1="16" x2="30" y2="16"/><line x1="6" y1="6" x2="9" y2="9"/><line x1="23" y1="9" x2="26" y2="6"/><line x1="6" y1="26" x2="9" y2="23"/><line x1="23" y1="23" x2="26" y2="26"/></g><circle cx="16" cy="16" r="11" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M11.5 23V9h5.2c2.5 0 4.3 1.7 4.3 4.2 0 2.5-1.8 4.2-4.3 4.2H14V23h-2.5zm2.5-8.6h2.6c1.1 0 1.9-.7 1.9-1.9s-.8-1.9-1.9-1.9H14v3.8z" fill="currentColor"/></svg>',
    glass:       '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="glaStroke" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="currentColor" stop-opacity="0.5"/><stop offset="100%" stop-color="currentColor" stop-opacity="1"/></linearGradient></defs><rect x="1.5" y="1.5" width="29" height="29" rx="4" fill="none" stroke="url(#glaStroke)" stroke-width="1.5"/><path d="M10 23V9h6.2c2.7 0 4.6 1.8 4.6 4.4 0 2.6-1.9 4.4-4.6 4.4H13V23h-3zm3-8.6h3.1c1.2 0 2-.8 2-2s-.8-2-2-2H13v4z" fill="currentColor"/></svg>',
    handcrafted: '<svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true"><path d="M3 4 Q5 2 7 3 Q15 4 25 3 Q28 2 29 5 Q30 14 29 26 Q28 30 25 29 Q15 28 7 29 Q4 30 3 27 Q2 14 3 4Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M11 23 Q10.5 22.5 11 21 L11 10 Q11 9.5 11.5 9.2 L17 9.2 Q20 9.5 20.5 13 Q20.5 16.5 17.5 17 L13.5 17 Q13.2 17.5 13.5 21 L13.5 23 Q12 23.2 11 23 Z M13.5 14.8 Q13.2 14.5 13.5 11.5 L16 11.5 Q17.5 11.5 17.8 12.8 Q18 14.5 16 14.8 Z" fill="currentColor"/></svg>'
  };

  // ---------------------------------------------------------------
  // Lazy font loader — preload all theme fonts ONCE when popover opens
  // ---------------------------------------------------------------
  var fontsLoaded = false;
  function loadAllThemeFonts() {
    if (fontsLoaded) return;
    fontsLoaded = true;

    THEMES.forEach(function (theme) {
      if (!theme.fonts) return; // editorial already in HTML head

      // Each theme.fonts string is one or more "Family:wght@x;y" separated by '|'
      var families = theme.fonts.split('|').map(function (f) { return 'family=' + f; }).join('&');
      var href = 'https://fonts.googleapis.com/css2?' + families + '&display=swap';

      var link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      document.head.appendChild(link);
    });
  }

  // ---------------------------------------------------------------
  // Detect initial theme
  // ---------------------------------------------------------------
  function detectTheme() {
    var params = new URLSearchParams(window.location.search);
    var urlTheme = params.get('theme');
    if (urlTheme && THEMES.some(function (t) { return t.id === urlTheme; })) return urlTheme;

    var stored = null;
    try { stored = localStorage.getItem(STORAGE_KEY); } catch (e) {}
    if (stored && THEMES.some(function (t) { return t.id === stored; })) return stored;

    return DEFAULT_THEME;
  }

  // ---------------------------------------------------------------
  // Apply theme
  // ---------------------------------------------------------------
  function applyTheme(themeId, animateColors) {
    var theme = THEMES.find(function (t) { return t.id === themeId; });
    if (!theme) return;

    // Smooth color transition (only on user-triggered switch, not initial)
    if (animateColors) {
      document.body.classList.add('theme-transitioning');
      setTimeout(function () {
        document.body.classList.remove('theme-transitioning');
      }, 450);
    }

    document.documentElement.setAttribute('data-theme', themeId);

    // Swap nav-mark P-logo
    var navMark = document.querySelector('.nav-mark svg');
    if (navMark && LOGOS[themeId]) {
      var wrap = document.createElement('div');
      wrap.innerHTML = LOGOS[themeId];
      var newSvg = wrap.firstChild;
      navMark.replaceWith(newSvg);
    }

    // Update theme-color meta for browser chrome
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme.bg);

    // Update trigger button display
    updateTriggerState(themeId);
  }

  function updateTriggerState(themeId) {
    var theme = THEMES.find(function (t) { return t.id === themeId; });
    if (!theme) return;

    var num = document.querySelector('.theme-trigger-num');
    var code = document.querySelector('.theme-trigger-code');
    if (num) num.textContent = theme.num;
    if (code) code.textContent = theme.code;

    document.querySelectorAll('[data-theme-option]').forEach(function (tile) {
      var isCurrent = tile.getAttribute('data-theme-option') === themeId;
      tile.setAttribute('aria-selected', isCurrent ? 'true' : 'false');
      tile.setAttribute('data-current', isCurrent ? 'true' : 'false');
    });
  }

  // ---------------------------------------------------------------
  // Switch theme (user-triggered)
  // ---------------------------------------------------------------
  function switchTheme(themeId) {
    applyTheme(themeId, true);

    try { localStorage.setItem(STORAGE_KEY, themeId); } catch (e) {}

    var url = new URL(window.location.href);
    if (themeId === DEFAULT_THEME) {
      url.searchParams.delete('theme');
    } else {
      url.searchParams.set('theme', themeId);
    }
    window.history.replaceState({}, '', url.toString());
  }

  // ---------------------------------------------------------------
  // Build popover (tiles) and wire interactions
  // ---------------------------------------------------------------
  function buildPopover(currentTheme) {
    var mount = document.querySelector('[data-theme-switcher]');
    if (!mount) return;

    var grid = mount.querySelector('.theme-grid');
    if (!grid) return;

    // Tiles
    grid.innerHTML = THEMES.map(function (theme) {
      return [
        '<button type="button" class="theme-tile" role="option" data-theme-option="' + theme.id + '"',
        '        aria-selected="' + (theme.id === currentTheme ? 'true' : 'false') + '"',
        '        title="' + theme.name + ' — ' + theme.code + '"',
        '        style="--tile-bg:' + theme.bg + ';--tile-ink:' + theme.ink + ';--tile-accent:' + theme.accent + ';--tile-muted:' + theme.muted + ';">',
        '  <span class="theme-tile-num">' + theme.num + '</span>',
        '  <span class="theme-tile-dot" aria-hidden="true"></span>',
        '  <span class="theme-tile-code">' + theme.code + '</span>',
        '</button>'
      ].join('');
    }).join('');

    var trigger = mount.querySelector('.theme-trigger');
    var popover = mount.querySelector('.theme-popover');

    // Trigger toggle — also preload fonts on first open
    trigger.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = mount.getAttribute('data-open') === 'true';
      mount.setAttribute('data-open', isOpen ? 'false' : 'true');
      trigger.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
      if (!isOpen) loadAllThemeFonts();
    });

    // Click outside to close
    document.addEventListener('click', function () {
      mount.setAttribute('data-open', 'false');
      trigger.setAttribute('aria-expanded', 'false');
    });
    popover.addEventListener('click', function (e) { e.stopPropagation(); });

    // Tile clicks — switch theme
    grid.querySelectorAll('[data-theme-option]').forEach(function (tile) {
      tile.addEventListener('click', function () {
        switchTheme(tile.getAttribute('data-theme-option'));
        // Keep popover open briefly so user sees the swap, then close
        setTimeout(function () {
          mount.setAttribute('data-open', 'false');
          trigger.setAttribute('aria-expanded', 'false');
          trigger.focus();
        }, 350);
      });

      // Keyboard navigation within tile grid
      tile.addEventListener('keydown', function (e) {
        var tiles = grid.querySelectorAll('[data-theme-option]');
        var idx = Array.prototype.indexOf.call(tiles, tile);
        var COLS = 4;
        var next;
        if (e.key === 'ArrowRight') next = tiles[(idx + 1) % tiles.length];
        else if (e.key === 'ArrowLeft') next = tiles[(idx - 1 + tiles.length) % tiles.length];
        else if (e.key === 'ArrowDown') next = tiles[Math.min(idx + COLS, tiles.length - 1)];
        else if (e.key === 'ArrowUp') next = tiles[Math.max(idx - COLS, 0)];
        else if (e.key === 'Home') next = tiles[0];
        else if (e.key === 'End') next = tiles[tiles.length - 1];
        else if (e.key === 'Escape') {
          e.preventDefault();
          mount.setAttribute('data-open', 'false');
          trigger.setAttribute('aria-expanded', 'false');
          trigger.focus();
          return;
        }
        if (next) { e.preventDefault(); next.focus(); }
      });
    });

    // Keyboard open on trigger
    trigger.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        mount.setAttribute('data-open', 'true');
        trigger.setAttribute('aria-expanded', 'true');
        loadAllThemeFonts();
        var first = grid.querySelector('[data-theme-option]');
        if (first) first.focus();
      }
    });

    // Generate-custom-via-perfect-ui link (Option B hybrid)
    var customLink = mount.querySelector('[data-theme-generate]');
    if (customLink) {
      customLink.addEventListener('click', function (e) {
        e.preventDefault();
        var theme = THEMES.find(function (t) { return t.id === document.documentElement.getAttribute('data-theme'); });
        var prompt = '/perfect-ui --redesign --vibe=' + (theme ? theme.id : 'editorial') + ' --type=docs --no-3d --stack=vanilla';

        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(prompt).then(function () {
            customLink.setAttribute('data-copied', 'true');
            customLink.textContent = 'Prompt copied — paste in Claude Code';
            setTimeout(function () {
              customLink.removeAttribute('data-copied');
              customLink.textContent = 'Generate custom via perfect-ui →';
            }, 2400);
          });
        }
      });
    }
  }

  // ---------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------
  var initial = detectTheme();
  applyTheme(initial, false);
  buildPopover(initial);
})();
