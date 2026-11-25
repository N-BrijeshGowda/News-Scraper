(function () {
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  // theme switcher (initial theme comes from an inline script in <head>)
  var setTheme = function (t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('ns-theme2', t); } catch (e) {}
    $$('.themes button').forEach(function (b) { b.classList.toggle('on', b.dataset.t === t); });
  };
  $$('.themes button').forEach(function (b) { b.addEventListener('click', function () { setTheme(b.dataset.t); }); });
  setTheme(document.documentElement.dataset.theme || 'lagoon');

  // live run status
  var rs = $('#runstate');
  if (rs) {
    var dot = $('.pulse', rs), txt = $('.txt', rs), fill = $('#progfill'),
        btn = $('#runbtn'), was = rs.dataset.running === '1';
    var poll = function () {
      fetch('/status').then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
        if (!s) return;
        var on = s.status === 'running';
        dot.classList.toggle('hide', !on);
        if (btn) btn.disabled = on;
        if (s.status === 'idle') { txt.textContent = 'no runs yet'; return; }
        fill.style.width = on ? (s.total ? Math.max(4, Math.round(100 * s.done / s.total)) : 4) + '%' : '0';
        txt.textContent = on ? s.phase.replace(/^(Source|Search): /, '') + ' ' + s.done + '/' + s.total
          : s.status === 'done' ? 'last run +' + s.added : 'last run failed';
        if (was && !on && document.body.dataset.page === 'home') location.reload();
        was = on;
      }).catch(function () {});
    };
    poll(); setInterval(poll, 2500);
  }

  // numbers count up once
  $$('[data-count]').forEach(function (el) {
    var end = parseInt(el.dataset.count, 10) || 0;
    if (calm || end < 2) { el.textContent = end; return; }
    var t0 = null;
    var step = function (t) {
      t0 = t0 || t;
      var p = Math.min(1, (t - t0) / 900), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(end * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });

  // ---------- home: board, reader drawer, search palette ----------
  var board = $('#board');
  if (board) {
    // lane type filter
    var chips = $$('.chip[data-kind]');
    chips.forEach(function (c) {
      c.addEventListener('click', function () {
        chips.forEach(function (x) { x.classList.toggle('on', x === c); });
        $$('.lane', board).forEach(function (l) {
          l.classList.toggle('hide', c.dataset.kind !== 'all' && l.dataset.kind !== c.dataset.kind);
        });
        board.scrollTo({ left: 0 });
      });
    });
    var by = function (dir) { board.scrollBy({ left: dir * 384, behavior: calm ? 'auto' : 'smooth' }); };
    var pv = $('#lprev'), nx = $('#lnext');
    if (pv) pv.addEventListener('click', function () { by(-1); });
    if (nx) nx.addEventListener('click', function () { by(1); });

    // reader drawer
    var drawer = $('#drawer'), scrim = $('#scrim'), opener = null;
    // video transcript with screenshots placed along the timeline
    var transTimer = null, curId = null, lastJson = '';
    var stopTrans = function () { clearInterval(transTimer); transTimer = null; curId = null; };
    var fmt = function (t) {
      t = Math.floor(t);
      var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
      return (h ? h + ':' + (m < 10 ? '0' : '') : '') + m + ':' + (s < 10 ? '0' : '') + s;
    };
    var seek = function (t) {
      var fr = $('#dmedia iframe'); if (!fr) return;
      var send = function (func, args) { fr.contentWindow.postMessage(JSON.stringify({ event: 'command', func: func, args: args }), '*'); };
      send('seekTo', [t, true]); send('playVideo', []);
      $('#dmedia').scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'nearest' });
    };
    var tsButton = function (t) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'ts'; b.textContent = fmt(t);
      b.setAttribute('aria-label', 'Play from ' + fmt(t));
      b.addEventListener('click', function () { seek(t); });
      return b;
    };
    var renderTrans = function (d, id) {
      var key = JSON.stringify(d); if (key === lastJson) return; lastJson = key;
      var st = $('#tstatus'), body = $('#tbody');
      st.innerHTML = ''; body.innerHTML = '';
      var busy = ['queued', 'transcript', 'frames', 'vision'].indexOf(d.proc) > -1;
      if (busy) {
        var p = document.createElement('span'); p.className = 'pulse'; st.appendChild(p);
        st.appendChild(document.createTextNode(' ' + d.label + '...'));
      }
      if (d.note) { var n = document.createElement('p'); n.className = 'mute small'; n.textContent = d.note; st.appendChild(n); }
      if (!busy && (d.proc === 'error' || !d.segments.length)) {
        var rb = document.createElement('button'); rb.type = 'button'; rb.className = 'btn ghost sm'; rb.textContent = 'Try again';
        rb.addEventListener('click', function () {
          var fd = new FormData(); fd.append('csrf', $('meta[name=csrf]').content);
          fetch('/videos/' + id + '/reprocess', { method: 'POST', body: fd }).then(function () { loadTrans(id); });
        });
        st.appendChild(rb);
      }
      var shots = d.shots.slice().sort(function (a, b) { return a.t - b.t; }), p2 = 0;
      var addShot = function (s) {
        var f = document.createElement('figure'), a = document.createElement('a'), im = document.createElement('img'), c = document.createElement('figcaption');
        a.href = s.url; a.target = '_blank'; a.rel = 'noopener'; im.src = s.url; im.alt = s.caption || s.kind || 'Screenshot'; im.loading = 'lazy';
        a.appendChild(im); f.appendChild(a);
        c.appendChild(tsButton(s.t));
        c.appendChild(document.createTextNode(' ' + [s.kind, s.caption].filter(Boolean).join(': ')));
        f.appendChild(c); body.appendChild(f);
      };
      if (d.segments.length) {
        while (p2 < shots.length && shots[p2].t < d.segments[0].t) addShot(shots[p2++]);
        d.segments.forEach(function (seg, i) {
          var row = document.createElement('div'), tx = document.createElement('p');
          row.className = 'tline'; tx.textContent = seg.text;
          row.appendChild(tsButton(seg.t)); row.appendChild(tx); body.appendChild(row);
          var nextT = i + 1 < d.segments.length ? d.segments[i + 1].t : Infinity;
          while (p2 < shots.length && shots[p2].t < nextT) addShot(shots[p2++]);
        });
      } else { shots.forEach(addShot); }
    };
    var loadTrans = function (id) {
      stopTrans(); curId = id; lastJson = '';
      $('#dtrans').classList.remove('hide');
      var tick = function () {
        fetch('/items/' + id + '/video').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
          if (!d || curId !== id) return;
          renderTrans(d, id);
          if (['queued', 'transcript', 'frames', 'vision'].indexOf(d.proc) === -1) { clearInterval(transTimer); transTimer = null; }
        }).catch(function () {});
      };
      tick(); transTimer = setInterval(tick, 3000);
    };

    var openStory = function (story, from) {
      opener = from || document.activeElement;
      $('#dtitle').textContent = story.dataset.title;
      $('#dmeta').textContent = story.dataset.meta;
      $('#dtext').textContent = $('.full', story).textContent;

      // picture or embedded YouTube player on top, article pictures underneath
      var media = $('#dmedia'), gal = $('#dgallery');
      media.innerHTML = ''; gal.innerHTML = '';
      if (story.dataset.video) {
        var box = document.createElement('div'), fr = document.createElement('iframe');
        box.className = 'vid';
        fr.src = 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(story.dataset.video) + '?rel=0&enablejsapi=1&origin=' + encodeURIComponent(location.origin);
        fr.title = story.dataset.title;
        fr.allow = 'encrypted-media; picture-in-picture; fullscreen';
        fr.allowFullscreen = true;
        fr.referrerPolicy = 'strict-origin-when-cross-origin';
        box.appendChild(fr); media.appendChild(box);
      } else if (story.dataset.image) {
        var im = document.createElement('img');
        im.src = story.dataset.image; im.alt = '';
        media.appendChild(im);
      }
      stopTrans(); $('#dtrans').classList.add('hide');
      if (story.dataset.video && story.dataset.id) loadTrans(story.dataset.id);
      $$('.imgs span', story).forEach(function (sp) {
        var a = document.createElement('a'), g = document.createElement('img');
        a.href = sp.dataset.src; a.target = '_blank'; a.rel = 'noopener';
        g.src = sp.dataset.src; g.alt = ''; g.loading = 'lazy';
        a.appendChild(g); gal.appendChild(a);
      });
      var a = $('#dopen');
      if (story.dataset.link) { a.href = story.dataset.link; a.classList.remove('hide'); } else { a.classList.add('hide'); }
      drawer.classList.add('open'); scrim.classList.add('open');
      document.documentElement.classList.add('reader-open');
      drawer.setAttribute('aria-hidden', 'false');
      drawer.scrollTop = 0;
      $('#dclose').focus();
    };
    drawer.addEventListener('scroll', function () {
      drawer.classList.toggle('scrolled', drawer.scrollTop > 20);
    }, { passive: true });
    var closeDrawer = function () {
      drawer.classList.remove('scrolled');
      $('#dmedia').innerHTML = '';  // also stops a playing video
      stopTrans();
      drawer.classList.remove('open'); scrim.classList.remove('open');
      document.documentElement.classList.remove('reader-open');
      drawer.setAttribute('aria-hidden', 'true');
      if (opener && opener.focus) opener.focus();
    };
    $$('.story .open').forEach(function (b) {
      b.addEventListener('click', function () { openStory(b.closest('.story'), b); });
    });
    $('#dclose').addEventListener('click', closeDrawer);
    scrim.addEventListener('click', function () { closeDrawer(); closePalette(); });

    // palette (search across every story)
    var pal = $('#palette'), pq = $('#pq'), plist = $('#plist'), stories = $$('.story'), sel = 0, shown = [];
    var render = function () {
      var term = pq.value.trim().toLowerCase();
      shown = stories.filter(function (s) { return !term || s.dataset.q.indexOf(term) > -1; }).slice(0, 30);
      sel = 0; plist.innerHTML = '';
      if (!shown.length) { var h = document.createElement('li'); h.className = 'hint'; h.textContent = 'No stories match.'; plist.appendChild(h); return; }
      shown.forEach(function (s, i) {
        var li = document.createElement('li'), b = document.createElement('button'), m = document.createElement('span');
        b.type = 'button'; b.textContent = s.dataset.title; m.textContent = s.dataset.meta; b.appendChild(m);
        if (i === 0) b.classList.add('sel');
        b.addEventListener('click', function () { closePalette(); openStory(s, null); });
        li.appendChild(b); plist.appendChild(li);
      });
    };
    var openPalette = function () { pal.classList.add('open'); scrim.classList.add('open'); pq.value = ''; render(); pq.focus(); };
    function closePalette() { pal.classList.remove('open'); if (!drawer.classList.contains('open')) scrim.classList.remove('open'); }
    var move = function (d) {
      var items = $$('button', plist); if (!items.length) return;
      items[sel].classList.remove('sel'); sel = (sel + d + items.length) % items.length;
      items[sel].classList.add('sel'); items[sel].scrollIntoView({ block: 'nearest' });
    };
    pq.addEventListener('input', render);
    pq.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter' && shown[sel]) { e.preventDefault(); var s = shown[sel]; closePalette(); openStory(s, null); }
    });
    // spotlight: opens the scraped story in the reader, rotates every 30 seconds
    var spot = $('#spot');
    if (spot) {
      var slides = $$('.slide', spot), dots = $$('.dot', spot), prog = $('.spotprog', spot),
          DUR = 30000, cur = 0, elapsed = 0, last = Date.now(), hover = false, userPaused = false,
          pbtn = $('#spotpause');
      spot.style.setProperty('--dur', (DUR / 1000) + 's');
      var restartBar = function () {
        if (!prog) return;
        prog.classList.remove('run'); void prog.offsetWidth; prog.classList.add('run');
      };
      var show = function (n) {
        cur = (n + slides.length) % slides.length;
        slides.forEach(function (s, i) {
          s.classList.toggle('on', i === cur);
          s.setAttribute('aria-hidden', i === cur ? 'false' : 'true');
          var b = $('.open', s); if (b) b.tabIndex = i === cur ? 0 : -1;
        });
        dots.forEach(function (d, i) { d.classList.toggle('on', i === cur); });
        elapsed = 0; restartBar();
      };
      slides.forEach(function (s) {
        $$('.open', s).forEach(function (b) { b.addEventListener('click', function (e) { openStory(s, e.currentTarget); }); });
      });
      $('#spotread').addEventListener('click', function (e) { openStory(slides[cur], e.currentTarget); });
      dots.forEach(function (d, i) { d.addEventListener('click', function () { show(i); }); });
      var pv2 = $('#spotprev'), nx2 = $('#spotnext');
      if (pv2) pv2.addEventListener('click', function () { show(cur - 1); });
      if (nx2) nx2.addEventListener('click', function () { show(cur + 1); });
      if (pbtn) pbtn.addEventListener('click', function () {
        userPaused = !userPaused;
        pbtn.setAttribute('aria-pressed', userPaused);
        pbtn.setAttribute('aria-label', userPaused ? 'Resume rotation' : 'Pause rotation');
        $('i', pbtn).className = 'ph ' + (userPaused ? 'ph-play' : 'ph-pause');
      });
      spot.addEventListener('mouseenter', function () { hover = true; });
      spot.addEventListener('mouseleave', function () { hover = false; });
      var shouldPause = function () {
        return userPaused || hover || document.hidden || !!spot.querySelector(':focus-visible') ||
          drawer.classList.contains('open') || pal.classList.contains('open');
      };
      if (slides.length > 1) {
        restartBar();
        setInterval(function () {
          var now = Date.now(), dt = now - last; last = now;
          var paused = shouldPause();
          spot.classList.toggle('paused', paused);
          if (paused) return;
          elapsed += dt;
          if (elapsed >= DUR) show(cur + 1);
        }, 250);
      }
    }

    var ob = $('#opensearch'); if (ob) ob.addEventListener('click', openPalette);
    document.addEventListener('keydown', function (e) {
      var t = document.activeElement.tagName;
      if (e.key === 'Escape') { closeDrawer(); closePalette(); }
      else if ((e.key === '/' && t !== 'INPUT' && t !== 'TEXTAREA') || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k')) { e.preventDefault(); openPalette(); }
    });
  }

  // ---------- add page ----------
  var picks = $$('.pick');
  var choose = function (p) {
    picks.forEach(function (x) { x.classList.toggle('on', x === p); x.setAttribute('aria-pressed', x === p); });
    $$('.pane').forEach(function (pn) { pn.classList.toggle('hide', pn.id !== p.dataset.pane); });
    history.replaceState(null, '', '#' + p.dataset.pane);
  };
  picks.forEach(function (p) { p.addEventListener('click', function () { choose(p); }); });
  picks.forEach(function (p) { if (location.hash === '#' + p.dataset.pane) choose(p); });

  var preview = function (area, out, split, isUrl) {
    if (!area || !out) return;
    var run = function () {
      var parts = area.value.split(split).map(function (s) { return s.trim(); }).filter(Boolean);
      parts = parts.filter(function (v, i) { return parts.indexOf(v) === i; });
      out.innerHTML = '';
      parts.slice(0, 12).forEach(function (p) {
        var s = document.createElement('span');
        s.className = 'pill ' + (isUrl && p.indexOf('.') < 0 ? 'err' : 'ok');
        s.textContent = p; out.appendChild(s);
      });
      if (parts.length > 12) { var m = document.createElement('span'); m.className = 'pill'; m.textContent = '+' + (parts.length - 12) + ' more'; out.appendChild(m); }
    };
    area.addEventListener('input', run); run();
  };
  preview($('#urls'), $('#urls-parsed'), /[\s,;]+/, true);
  preview($('#topics'), $('#topics-parsed'), /[\n,;]+/, false);
})();
