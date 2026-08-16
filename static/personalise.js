/* ============================================================================
   Personalise — visitor display preferences.

   This replaces the earlier "Try colours" demo. The distinction matters: a
   colour switcher advertises that a site is a template, whereas appearance,
   text size, contrast and motion are controls a professional tool is expected
   to offer. Several of them are accessibility requirements rather than taste.

   Six settings, all applied as attributes or custom properties on <html>:

     scheme     light | dark | system      surfaces
     accent     one of ACCENTS             primary colour and everything derived
     text       100 | 112 | 125            root font size
     contrast   normal | more              secondary text and borders
     motion     full | reduce              transitions and section reveals
     density    comfortable | compact      vertical rhythm and card padding

   Two things are deliberate. Only the *ids* are stored, never derived colours —
   an earlier version cached whole palettes and browsers held stale copies of
   them for weeks. And nothing persists unless the visitor asks it to.
   ============================================================================ */
(function () {
  'use strict';

  var KEY = 'cs-prefs';
  var REMEMBER = 'cs-prefs-remember';

  var DEFAULTS = {
    scheme: 'system', accent: 'claimsight', text: '100',
    contrast: 'normal', motion: 'full', density: 'comfortable'
  };

  /* Accent only sets the primary and its companion glow. Surfaces come from the
     scheme, so an accent works in both light and dark rather than dragging its
     own background along. */
  var ACCENTS = [
    { id: 'claimsight',    name: 'ClaimSight', primary: '#2563EB', glow: '#0D9488' },
    { id: 'electric-blue', name: 'Azure',      primary: '#3B9EFF', glow: '#5CB0FF' },
    { id: 'tarmac',        name: 'Teal',       primary: '#14B8A6', glow: '#2DD4BF' },
    { id: 'willow',        name: 'Sage',       primary: '#8FAE6B', glow: '#A8C285' },
    { id: 'culture',       name: 'Lime',       primary: '#C5E13F', glow: '#C5E13F' },
    { id: 'basalt',        name: 'Amber',      primary: '#D97706', glow: '#F59E0B' },
    { id: 'rivet',         name: 'Signal',     primary: '#F97316', glow: '#FB923C' },
    { id: 'dune',          name: 'Terracotta', primary: '#C47A4A', glow: '#D49264' },
    { id: 'leadpages',     name: 'Rust',       primary: '#C85A2C', glow: '#C85A2C' },
    { id: 'petal',         name: 'Rose',       primary: '#E8A0A8', glow: '#F0B7BD' },
    { id: 'orchid',        name: 'Mauve',      primary: '#B28BB8', glow: '#C9A5CE' },
    { id: 'neon-pink',     name: 'Magenta',    primary: '#FF4DA6', glow: '#FF6BB8' }
  ];

  var INK_LIGHT = '#071B30';
  var INK_DARK = '#EAF1F8';

  // ------------------------------------------------------------ colour maths
  function hex(c) {
    c = c.replace('#', '');
    if (c.length === 3) c = c[0] + c[0] + c[1] + c[1] + c[2] + c[2];
    return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
  }
  function toHex(v) {
    return '#' + v.map(function (x) {
      return Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, '0');
    }).join('');
  }
  function mix(a, b, t) {
    var x = hex(a), y = hex(b);
    return toHex([0, 1, 2].map(function (i) { return x[i] + (y[i] - x[i]) * t; }));
  }
  function rgba(c, a) { var v = hex(c); return 'rgba(' + v[0] + ',' + v[1] + ',' + v[2] + ',' + a + ')'; }
  function lum(c) {
    var v = hex(c).map(function (x) {
      x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function contrast(a, b) {
    var l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  function readableOn(bg, ink) {
    return contrast(bg, '#ffffff') >= contrast(bg, ink) ? '#ffffff' : ink;
  }
  /* Step a colour toward a target until it clears a contrast ratio on `on`.
     Used everywhere a themed colour has to carry text: a pale accent is fine on
     a button and unreadable as body copy, and only measurement tells them
     apart. */
  function until(colour, on, target, toward) {
    target = target + 0.6;                    // overshoot so nothing lands on the line
    var out = colour, guard = 0;
    while (contrast(out, on) < target && guard++ < 40) out = mix(out, toward, 0.09);
    return contrast(out, on) < target ? toward : out;
  }

  // ------------------------------------------------------------------- apply
  function resolveScheme(scheme) {
    if (scheme === 'system') {
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark' : 'light';
    }
    return scheme;
  }

  function accentById(id) {
    for (var i = 0; i < ACCENTS.length; i++) if (ACCENTS[i].id === id) return ACCENTS[i];
    return ACCENTS[0];
  }

  function apply(p) {
    var el = document.documentElement, r = el.style;
    var scheme = resolveScheme(p.scheme);
    var dark = scheme === 'dark';
    var a = accentById(p.accent);
    var primary = a.primary, glow = a.glow;
    var ink = dark ? INK_DARK : INK_LIGHT;
    var page = dark ? '#101D2B' : '#ffffff';
    var soft = dark ? '#0D1826' : '#F8FAFC';   // worst-case ground for text

    el.setAttribute('data-scheme', scheme);
    el.setAttribute('data-contrast', p.contrast);
    el.setAttribute('data-motion', p.motion);
    el.setAttribute('data-density', p.density);
    el.setAttribute('data-accent', p.accent);
    r.setProperty('font-size', p.text === '100' ? '' : (p.text / 100 * 16) + 'px');

    r.setProperty('--blue-600', primary);
    r.setProperty('--blue-500', mix(primary, '#ffffff', 0.16));
    r.setProperty('--blue-100', mix(primary, page, dark ? 0.80 : 0.90));
    r.setProperty('--on-primary', readableOn(primary, INK_LIGHT));
    r.setProperty('--primary-text', until(primary, soft, 4.5, ink));
    r.setProperty('--tick', until(primary, soft, 4.5, ink));
    r.setProperty('--ai-600', glow);
    r.setProperty('--ai-100', mix(glow, page, dark ? 0.80 : 0.88));
    // the glow as text on its own pale tint — measured, not assumed
    // the audit "Settlement" badge: its own tint, measured against its own text
    var tagBg = mix(primary, page, dark ? 0.84 : 0.90);
    r.setProperty('--tag-s-bg', tagBg);
    r.setProperty('--tag-s-fg', until(primary, tagBg, 4.5, ink));
    r.setProperty('--ai-text', until(glow, mix(glow, soft, dark ? 0.80 : 0.88), 4.5, ink));
    r.setProperty('--text-secondary', until(dark ? '#A3B6C9' : '#5C6D82', soft, 4.5, ink));
    r.setProperty('--text-muted', until(dark ? '#93A7BC' : '#64748A', soft, 4.5, ink));
    // status colours are fixed hues, but they still have to read on their own
    // tint in whichever scheme is active
    r.setProperty('--green-600', until('#12704D', mix('#12704D', soft, 0.88), 4.5, ink));
    r.setProperty('--green-100', mix('#12704D', page, 0.88));
    r.setProperty('--amber-600', until('#9A5B08', mix('#9A5B08', soft, 0.88), 4.5, ink));
    r.setProperty('--amber-100', mix('#9A5B08', page, 0.88));
    r.setProperty('--red-600', until('#B03636', mix('#B03636', soft, 0.88), 4.5, ink));
    r.setProperty('--red-100', mix('#B03636', page, 0.88));

    r.setProperty('--panel-a', mix(glow, page, dark ? 0.86 : 0.88));
    r.setProperty('--panel-b', mix(primary, page, dark ? 0.88 : 0.90));
    r.setProperty('--panel-b-line', mix(primary, page, 0.66));
    r.setProperty('--panel-b-fg', until(primary, soft, 4.5, ink));

    r.setProperty('--logo-a', primary);
    r.setProperty('--logo-b', glow);
    r.setProperty('--logo-ink', ink);

    /* The dark bands keep the deep ground in both schemes — they are the
       product's signature and inverting them would flatten the page. The accent
       only tints them. */
    var band = INK_LIGHT;
    r.setProperty('--band-bg',
      'radial-gradient(900px 500px at 74% 42%, ' + rgba(primary, 0.30) + ' 0%, transparent 62%),' +
      'radial-gradient(700px 420px at 12% 8%, ' + rgba(glow, 0.20) + ' 0%, transparent 58%),' + band);
    r.setProperty('--footer-bg', band);
    r.setProperty('--band-fg', '#ffffff');
    r.setProperty('--band-fg-2', 'rgba(255,255,255,.74)');
    r.setProperty('--band-fg-3', 'rgba(255,255,255,.56)');
    r.setProperty('--band-line', 'rgba(255,255,255,.22)');
    r.setProperty('--band-accent', until(mix(glow, '#ffffff', 0.40), band, 4.5, '#ffffff'));
    r.setProperty('--band-emph', until(mix(glow, '#ffffff', 0.55), band, 4.5, '#ffffff'));
    r.setProperty('--band-arrow', mix(primary, '#ffffff', 0.36));
    r.setProperty('--band-btn-bg', '#ffffff');
    r.setProperty('--band-btn-fg', INK_LIGHT);
    r.setProperty('--band-btn-hover', '#E9EFF7');
    r.setProperty('--logo-band-a', mix(primary, '#ffffff', 0.42));
    r.setProperty('--logo-band-b', mix(glow, '#ffffff', 0.26));
    r.setProperty('--panel-tint', 'linear-gradient(165deg,' + mix(primary, band, 0.62) + ',' + band + ')');
    r.setProperty('--panel-tint-line', mix(primary, band, 0.42));
    r.setProperty('--panel-tint-fg', '#ffffff');
    r.setProperty('--panel-tint-2', 'rgba(255,255,255,.66)');
    r.setProperty('--panel-tint-accent', mix(glow, '#ffffff', 0.45));
  }

  // ------------------------------------------------------------------- state
  function load() {
    var p = {};
    for (var k in DEFAULTS) p[k] = DEFAULTS[k];
    try {
      var raw = localStorage.getItem(KEY);
      if (raw && raw.charAt(0) === '{') {
        var saved = JSON.parse(raw);
        for (var j in DEFAULTS) if (saved[j]) p[j] = saved[j];
      }
    } catch (e) {}
    return p;
  }
  function remembering() {
    try { return localStorage.getItem(REMEMBER) !== '0'; } catch (e) { return false; }
  }
  function save(p) {
    try {
      if (remembering()) localStorage.setItem(KEY, JSON.stringify(p));
      else localStorage.removeItem(KEY);
    } catch (e) {}
  }

  var prefs = load();
  // Honour the OS setting before any UI exists, so nothing flashes.
  if (prefs.motion === 'full' && window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches) prefs.motion = 'reduce';
  apply(prefs);

  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    var onChange = function () { if (prefs.scheme === 'system') apply(prefs); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  // --------------------------------------------------------------------- UI
  function segment(label, name, options, hint) {
    var wrap = document.createElement('div');
    wrap.className = 'cs-p-row';
    var h = '<div class="cs-p-label">' + label +
      (hint ? '<span>' + hint + '</span>' : '') + '</div><div class="cs-p-seg" role="group" aria-label="' +
      label + '">';
    options.forEach(function (o) {
      h += '<button type="button" role="radio" data-v="' + o[0] + '" aria-checked="' +
        (prefs[name] === o[0]) + '"' + (prefs[name] === o[0] ? ' class="on"' : '') + '>' +
        o[1] + '</button>';
    });
    wrap.innerHTML = h + '</div>';
    wrap.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () {
        prefs[name] = b.dataset.v;
        wrap.querySelectorAll('button').forEach(function (x) {
          x.classList.remove('on'); x.setAttribute('aria-checked', 'false');
        });
        b.classList.add('on'); b.setAttribute('aria-checked', 'true');
        apply(prefs); save(prefs);
      });
    });
    return wrap;
  }

  function build() {
    var fab = document.createElement('button');
    fab.className = 'cs-p-fab';
    fab.type = 'button';
    fab.setAttribute('aria-expanded', 'false');
    fab.innerHTML =
      '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">' +
      '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/>' +
      '<path d="M12 3a9 9 0 0 0 0 18Z" fill="currentColor"/></svg>Personalise';

    var panel = document.createElement('div');
    panel.className = 'cs-p-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Personalise your experience');
    panel.hidden = true;

    var head = document.createElement('div');
    head.className = 'cs-p-head';
    head.innerHTML = '<div><strong>Personalise your experience</strong>' +
      '<span>Display preferences for this browser only.</span></div>';
    var x = document.createElement('button');
    x.className = 'cs-p-x'; x.type = 'button';
    x.setAttribute('aria-label', 'Close'); x.textContent = '×';
    head.appendChild(x);
    panel.appendChild(head);

    var body = document.createElement('div');
    body.className = 'cs-p-body';

    body.appendChild(segment('Appearance', 'scheme',
      [['light', 'Light'], ['dark', 'Dark'], ['system', 'System']]));

    var acc = document.createElement('div');
    acc.className = 'cs-p-row';
    acc.innerHTML = '<div class="cs-p-label">Accent colour</div>';
    var sw = document.createElement('div');
    sw.className = 'cs-p-swatches';
    sw.setAttribute('role', 'group');
    sw.setAttribute('aria-label', 'Accent colour');
    ACCENTS.forEach(function (a) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'cs-p-sw' + (prefs.accent === a.id ? ' on' : '');
      b.style.background = a.primary;
      b.title = a.name;
      b.setAttribute('aria-label', a.name);
      b.setAttribute('aria-pressed', String(prefs.accent === a.id));
      b.addEventListener('click', function () {
        prefs.accent = a.id;
        sw.querySelectorAll('.cs-p-sw').forEach(function (o) {
          o.classList.remove('on'); o.setAttribute('aria-pressed', 'false');
        });
        b.classList.add('on'); b.setAttribute('aria-pressed', 'true');
        apply(prefs); save(prefs);
      });
      sw.appendChild(b);
    });
    acc.appendChild(sw);
    body.appendChild(acc);

    body.appendChild(segment('Text size', 'text',
      [['100', 'Default'], ['112', 'Large'], ['125', 'Larger']]));
    body.appendChild(segment('Contrast', 'contrast',
      [['normal', 'Standard'], ['more', 'Increased']]));
    body.appendChild(segment('Motion', 'motion',
      [['full', 'Full'], ['reduce', 'Reduced']]));
    body.appendChild(segment('Interface density', 'density',
      [['comfortable', 'Comfortable'], ['compact', 'Compact']]));
    panel.appendChild(body);

    var foot = document.createElement('div');
    foot.className = 'cs-p-foot';
    var rem = document.createElement('label');
    rem.className = 'cs-p-remember';
    rem.innerHTML = '<input type="checkbox"' + (remembering() ? ' checked' : '') +
      '><span>Remember my preferences</span>';
    rem.querySelector('input').addEventListener('change', function (e) {
      try {
        if (e.target.checked) { localStorage.setItem(REMEMBER, '1'); save(prefs); }
        else { localStorage.setItem(REMEMBER, '0'); localStorage.removeItem(KEY); }
      } catch (err) {}
    });
    var reset = document.createElement('button');
    reset.className = 'cs-p-reset'; reset.type = 'button'; reset.textContent = 'Reset to defaults';
    reset.addEventListener('click', function () {
      try { localStorage.removeItem(KEY); } catch (e) {}
      location.reload();
    });
    foot.appendChild(rem);
    foot.appendChild(reset);
    panel.appendChild(foot);

    function toggle(open) {
      panel.hidden = !open;
      fab.setAttribute('aria-expanded', String(open));
      if (open) panel.querySelector('button, input').focus();
    }
    fab.addEventListener('click', function () { toggle(panel.hidden); });
    x.addEventListener('click', function () { toggle(false); fab.focus(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !panel.hidden) { toggle(false); fab.focus(); }
    });

    document.body.appendChild(fab);
    document.body.appendChild(panel);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
