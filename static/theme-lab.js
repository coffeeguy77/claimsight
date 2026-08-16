/* ============================================================================
   Try colours — client-side theme lab.

   Ported from the LeadPages picker. That site has a six-token model
   (primary / ink / bg / surface / muted / glow); ClaimSight has a larger token
   set with derived tints, dark bands and semantic status colours. So rather
   than renaming variables, this derives the full ClaimSight palette from the
   six inputs: tints are mixed toward white, borders toward the background, and
   dark surfaces are stepped off the ink colour.

   Status colours (green / amber / red) are deliberately NOT themed. A "flagged
   for review" badge that turns lime because someone picked a palette is a
   usability bug, not a theme.

   Hidden by default — see shouldMount() at the bottom.
   ============================================================================ */
(function () {
  'use strict';

  var STORAGE_THEME = 'cs-theme';
  var STORAGE_LAB = 'cs-theme-lab';

  var DEFAULT = {
    id: 'claimsight', name: 'ClaimSight',
    blurb: 'Navy, white and signal blue. The shipped identity.',
    primary: '#2563EB', ink: '#071B30', bg: '#F3F6F9',
    surface: '#F8FAFC', muted: '#66788D', glow: '#0D9488'
  };

  var PRESETS = [
    { id: 'leadpages', flat: true, name: 'LeadPages', blurb: 'Charcoal, cream and signal orange.',
      primary: '#C85A2C', ink: '#0B1B2A', bg: '#F4EBDE', surface: '#EBDDCD', muted: '#6B7680', glow: '#C85A2C' },
    { id: 'culture', flat: true, name: 'Culture Lime', blurb: 'The signature forest and lime look.',
      primary: '#C5E13F', ink: '#0B2114', bg: '#FDFCF0', surface: '#F5F2E6', muted: '#5C6B60', glow: '#C5E13F' },
    { id: 'basalt', flat: true, name: 'Basalt', blurb: 'Charcoal ground with a copper spark.',
      primary: '#D97706', ink: '#141414', bg: '#F7F4EF', surface: '#EDE8E1', muted: '#6B6560', glow: '#F59E0B' },
    { id: 'rivet', name: 'Rivet', blurb: 'Deep navy with a sharp signal accent.',
      primary: '#F97316', ink: '#0B1C33', bg: '#F4F7FB', surface: '#E8EEF5', muted: '#5A6B7D', glow: '#FB923C' },
    { id: 'tarmac', flat: true, name: 'Tarmac', blurb: 'Near-black with an electric teal edge.',
      primary: '#14B8A6', ink: '#0A0F14', bg: '#F3F6F7', surface: '#E6ECEE', muted: '#5C6A70', glow: '#2DD4BF' },
    { id: 'petal', flat: true, name: 'Petal', blurb: 'Soft rose warmth on cream.',
      primary: '#E8A0A8', ink: '#3D2A32', bg: '#FFF8F7', surface: '#F8ECEC', muted: '#7A646A', glow: '#F0B7BD' },
    { id: 'willow', name: 'Willow', blurb: 'Calm sage and soft daylight green.',
      primary: '#8FAE6B', ink: '#243028', bg: '#F7F9F3', surface: '#EBEEE4', muted: '#66705F', glow: '#A8C285' },
    { id: 'orchid', flat: true, name: 'Orchid', blurb: 'Quiet mauve with a polished finish.',
      primary: '#B28BB8', ink: '#2C2130', bg: '#FBF7FC', surface: '#F1EAF3', muted: '#6F6274', glow: '#C9A5CE' },
    { id: 'dune', flat: true, name: 'Dune', blurb: 'Warm sand and terracotta.',
      primary: '#C47A4A', ink: '#2B2118', bg: '#FBF6EF', surface: '#F1E7DA', muted: '#736557', glow: '#D49264' },
    { id: 'neon-pink', name: 'Neon Pink', blurb: 'Dark pink neon on deep plum.',
      primary: '#FF4DA6', ink: '#140F14', bg: '#FFF0F7', surface: '#F8E0ED', muted: '#8F7084', glow: '#FF6BB8' },
    { id: 'electric-blue', name: 'Electric Blue', blurb: 'Electric blue on midnight navy.',
      primary: '#3B9EFF', ink: '#0A1524', bg: '#EEF5FF', surface: '#E0ECFA', muted: '#5A6B80', glow: '#5CB0FF' }
  ];

  // ------------------------------------------------------------ colour maths
  function hex(c) {
    c = c.replace('#', '');
    if (c.length === 3) c = c[0] + c[0] + c[1] + c[1] + c[2] + c[2];
    return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
  }
  function toHex(rgb) {
    return '#' + rgb.map(function (v) {
      return Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
    }).join('');
  }
  function mix(a, b, amount) {          // amount 0..1 toward b
    var x = hex(a), y = hex(b);
    return toHex([0, 1, 2].map(function (i) { return x[i] + (y[i] - x[i]) * amount; }));
  }
  function rgba(c, a) { var r = hex(c); return 'rgba(' + r[0] + ',' + r[1] + ',' + r[2] + ',' + a + ')'; }
  function luminance(c) {
    var r = hex(c).map(function (v) {
      v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r[0] + 0.7152 * r[1] + 0.0722 * r[2];
  }
  function contrast(a, b) {
    var l1 = luminance(a), l2 = luminance(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  // Text on a coloured button: pick whichever of white/ink actually reads.
  function readableOn(bg, ink) {
    return contrast(bg, '#ffffff') >= contrast(bg, ink) ? '#ffffff' : ink;
  }

  // -------------------------------------------------------------- apply theme
  function apply(theme) {
    var r = document.documentElement.style;
    var p = theme.primary, ink = theme.ink, bg = theme.bg, surf = theme.surface;
    var muted = theme.muted, glow = theme.glow;

    r.setProperty('--blue-600', p);
    r.setProperty('--blue-500', mix(p, '#ffffff', 0.16));
    r.setProperty('--blue-100', mix(p, '#ffffff', 0.90));
    r.setProperty('--on-primary', readableOn(p, ink));

    r.setProperty('--navy-950', ink);
    r.setProperty('--navy-900', mix(ink, '#ffffff', 0.07));
    r.setProperty('--navy-800', mix(ink, '#ffffff', 0.15));

    r.setProperty('--text-primary', mix(ink, '#ffffff', 0.06));
    r.setProperty('--text-secondary', muted);
    r.setProperty('--text-muted', mix(muted, '#ffffff', 0.26));

    r.setProperty('--app-bg', bg);
    r.setProperty('--surface', '#ffffff');
    r.setProperty('--surface-soft', surf);

    r.setProperty('--border', mix(ink, bg, 0.86));
    r.setProperty('--border-strong', mix(ink, bg, 0.74));

    r.setProperty('--ai-600', glow);
    r.setProperty('--ai-100', mix(glow, '#ffffff', 0.88));

    /* Dark bands — the evidence section, message ribbon, final CTA and footer.

       Two treatments, chosen per-theme:

       flat   the band IS the theme's primary, exactly as it appears on the
              buttons. No darkening: a Petal band should be Petal pink. Where
              the primary is too light for white text the foreground flips to
              the theme's ink instead, which is what keeps it readable.

       wash   the ink ground with radial washes of primary and glow. Right when
              the primary is already deep and saturated. */
    if (theme.flat) {
      var fg = readableOn(p, ink);
      var onLight = fg !== '#ffffff';
      // Where the best available foreground is only just readable, hold the
      // secondary text at a higher opacity so footer links stay legible.
      var tight = contrast(p, fg) < 4.5;
      var o2 = tight ? 0.88 : 0.72;
      var o3 = tight ? 0.74 : 0.55;
      r.setProperty('--band-bg', p);
      r.setProperty('--footer-bg', p);
      r.setProperty('--band-fg', fg);
      r.setProperty('--band-fg-2', onLight ? rgba(ink, o2) : 'rgba(255,255,255,' + o2 + ')');
      r.setProperty('--band-fg-3', onLight ? rgba(ink, o3) : 'rgba(255,255,255,' + o3 + ')');
      r.setProperty('--band-line', onLight ? rgba(ink, 0.20) : 'rgba(255,255,255,.24)');
      r.setProperty('--band-accent', onLight ? mix(ink, p, 0.30) : '#ffffff');
      r.setProperty('--band-arrow', onLight ? rgba(ink, 0.45) : 'rgba(255,255,255,.55)');
      // the recommended-value card sits on the band, so it steps off the ink
      r.setProperty('--panel-tint', mix(ink, p, 0.18));
      r.setProperty('--panel-tint-line', mix(ink, p, 0.42));
      r.setProperty('--panel-tint-fg', '#ffffff');
      r.setProperty('--panel-tint-2', 'rgba(255,255,255,.66)');
    } else {
      r.setProperty('--band-bg',
        'radial-gradient(900px 500px at 74% 42%, ' + rgba(p, 0.30) + ' 0%, transparent 62%),' +
        'radial-gradient(700px 420px at 12% 8%, ' + rgba(glow, 0.20) + ' 0%, transparent 58%),' + ink);
      r.setProperty('--footer-bg', ink);
      r.setProperty('--band-fg', '#ffffff');
      r.setProperty('--band-fg-2', 'rgba(255,255,255,.72)');
      r.setProperty('--band-fg-3', 'rgba(255,255,255,.55)');
      r.setProperty('--band-line', 'rgba(255,255,255,.20)');
      r.setProperty('--band-accent', mix(glow, '#ffffff', 0.34));
      r.setProperty('--band-arrow', mix(p, '#ffffff', 0.36));
      r.setProperty('--panel-tint', 'linear-gradient(165deg,' + mix(p, ink, 0.62) + ',' + ink + ')');
      r.setProperty('--panel-tint-line', mix(p, ink, 0.42));
      r.setProperty('--panel-tint-fg', '#ffffff');
      r.setProperty('--panel-tint-2', 'rgba(255,255,255,.66)');
    }

    document.documentElement.setAttribute('data-theme', theme.id);
    try { localStorage.setItem(STORAGE_THEME, JSON.stringify(theme)); } catch (e) {}
  }

  function restore() {
    try {
      var saved = JSON.parse(localStorage.getItem(STORAGE_THEME) || 'null');
      if (saved && saved.primary) { apply(saved); return saved.id; }
    } catch (e) {}
    return DEFAULT.id;
  }

  // ------------------------------------------------------------------- panel
  function build(activeId) {
    var all = [DEFAULT].concat(PRESETS);

    var fab = document.createElement('button');
    fab.className = 'cs-lab-fab';
    fab.type = 'button';
    fab.setAttribute('aria-expanded', 'false');
    fab.innerHTML = '<span class="cs-lab-swatch" aria-hidden="true"></span>Try colours';

    var panel = document.createElement('div');
    panel.className = 'cs-lab-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Colour themes');
    panel.hidden = true;

    var head = document.createElement('div');
    head.className = 'cs-lab-head';
    head.innerHTML = '<div><strong>Try colours</strong>' +
      '<span>Preview only. Nothing is saved for anyone else.</span></div>';
    var close = document.createElement('button');
    close.className = 'cs-lab-x'; close.type = 'button';
    close.setAttribute('aria-label', 'Close colour panel'); close.textContent = '×';
    head.appendChild(close);
    panel.appendChild(head);

    var list = document.createElement('div');
    list.className = 'cs-lab-list';
    all.forEach(function (t) {
      var b = document.createElement('button');
      b.className = 'cs-lab-item' + (t.id === activeId ? ' on' : '');
      b.type = 'button';
      b.dataset.id = t.id;
      b.innerHTML =
        '<span class="cs-lab-dots" aria-hidden="true">' +
          '<i style="background:' + t.ink + '"></i>' +
          '<i style="background:' + t.primary + '"></i>' +
          '<i style="background:' + t.glow + '"></i>' +
          '<i style="background:' + t.surface + '"></i>' +
        '</span>' +
        '<span class="cs-lab-txt"><b>' + t.name + '</b><em>' + t.blurb + '</em></span>';
      b.addEventListener('click', function () {
        apply(t);
        list.querySelectorAll('.cs-lab-item').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
      });
      list.appendChild(b);
    });
    panel.appendChild(list);

    var foot = document.createElement('div');
    foot.className = 'cs-lab-foot';
    var reset = document.createElement('button');
    reset.className = 'cs-lab-reset'; reset.type = 'button';
    reset.textContent = 'Reset to ClaimSight';
    reset.addEventListener('click', function () {
      try { localStorage.removeItem(STORAGE_THEME); } catch (e) {}
      location.reload();
    });
    foot.appendChild(reset);
    panel.appendChild(foot);

    function toggle(open) {
      panel.hidden = !open;
      fab.setAttribute('aria-expanded', String(open));
      if (open) panel.querySelector('.cs-lab-item').focus();
    }
    fab.addEventListener('click', function () { toggle(panel.hidden); });
    close.addEventListener('click', function () { toggle(false); fab.focus(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !panel.hidden) { toggle(false); fab.focus(); }
    });

    document.body.appendChild(fab);
    document.body.appendChild(panel);
  }

  /* Whether the picker is visible at all.
     It stays hidden for ordinary visitors: a colour switcher floating over a
     product sold on precision reads as a template demo. Turn it on with
     ?colours=1 (sticky thereafter), off with ?colours=0. */
  function shouldMount() {
    var q = new URLSearchParams(location.search);
    if (q.get('colours') === '1' || q.get('colors') === '1') {
      try { localStorage.setItem(STORAGE_LAB, '1'); } catch (e) {}
      return true;
    }
    if (q.get('colours') === '0' || q.get('colors') === '0') {
      try { localStorage.removeItem(STORAGE_LAB); } catch (e) {}
      return false;
    }
    try { return localStorage.getItem(STORAGE_LAB) === '1'; } catch (e) { return false; }
  }

  var activeId = restore();               // theme applies even when the UI is hidden
  if (!shouldMount()) return;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { build(activeId); });
  } else {
    build(activeId);
  }
})();
