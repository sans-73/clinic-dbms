/* Prescribing screen: type-ahead (green/blue/red with TEXT labels), editable defaults, allergy confirmation dialog. */
(function () {
  var form = document.getElementById('rxform');
  if (!form) return;
  var cfg = JSON.parse(document.getElementById('rxcfg').textContent);
  var rowsBox = document.getElementById('rows'), rowIds = document.getElementById('row_ids'), next = cfg.nextIndex;
  var STATE_TEXT = { green: 'In stock', blue: 'Out of stock', red: 'Not in catalog' };

  function ids() { return rowIds.value ? rowIds.value.split(',') : []; }
  function addId(i) { var a = ids(); a.push(String(i)); rowIds.value = a.join(','); }
  function el(h) { var d = document.createElement('div'); d.innerHTML = h.trim(); return d.firstChild; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

  function addRow(d) {
    d = d || {}; var i = next++; addId(i);
    var r = el('<div class="rx-row" data-i="' + i + '">' +
      '<div><label class="f"><span>Medicine (type 2+ letters)</span><input data-name autocomplete="off" value="' + esc(d.name || d.free_text || '') + '"></label>' +
      '<span class="chip" data-chip></span> <small class="errtext" data-warn></small></div>' +
      '<label class="f"><span>Dose</span><input name="dose_' + i + '" value="' + esc(d.dose) + '"></label>' +
      '<label class="f"><span>Schedule (1-0-1)</span><input name="schedule_' + i + '" value="' + esc(d.schedule) + '"></label>' +
      '<label class="f"><span>Food</span><input name="food_' + i + '" value="' + esc(d.food) + '" placeholder="after food"></label>' +
      '<label class="f"><span>Days</span><input type="number" min="1" name="days_' + i + '" value="' + esc(d.days) + '"></label>' +
      '<label class="f"><span>Qty</span><input type="number" min="0" step="any" name="qty_' + i + '" value="' + esc(d.qty) + '"></label>' +
      '<div><label class="small"><input type="checkbox" name="remove_' + i + '" value="1"> remove</label></div>' +
      '<input type="hidden" name="medicine_id_' + i + '" value="' + esc(d.medicine_id) + '">' +
      '<input type="hidden" name="free_text_' + i + '" value="' + esc(d.medicine_id ? '' : (d.free_text || '')) + '">' +
      '<input type="hidden" name="line_id_' + i + '" value="' + esc(d.line_id) + '">' +
      '<input type="hidden" name="confirm_' + i + '" value=""><input type="hidden" name="override_' + i + '" value=""></div>');
    rowsBox.appendChild(r);
    var name = r.querySelector('[data-name]');
    r._warn = d.warn || [];
    setChip(r, d.medicine_id ? (d.state || 'green') : (d.free_text ? 'red' : ''), d.label);
    attach(name, r);
    return r;
  }
  function setChip(r, state, label) {
    var c = r.querySelector('[data-chip]');
    c.className = 'chip ' + (state || ''); c.textContent = state ? (label || STATE_TEXT[state]) : '';
    r.querySelector('[data-warn]').textContent = (r._warn && r._warn.length) ? 'ALLERGY WARNING: ' + r._warn.join(', ') : '';
  }
  function setf(r, n, v) { var e = r.querySelector('[name=' + n + '_' + r.dataset.i + ']'); if (e && v != null && v !== '') e.value = v; }

  function attach(input, r) {
    var box = null, items = [], act = -1;
    function close() { if (box) { box.remove(); box = null; } }
    function pick(s) {
      r.querySelector('[name=medicine_id_' + r.dataset.i + ']').value = s.medicine_id || '';
      r.querySelector('[name=free_text_' + r.dataset.i + ']').value = '';
      input.value = s.name + (s.strength ? ' ' + s.strength : '') + (s.form ? ' (' + s.form + ')' : '');
      r._warn = s.allergy_warning || [];
      setf(r, 'dose', s.dose); setf(r, 'schedule', s.schedule); setf(r, 'food', s.food); setf(r, 'days', s.days);
      setChip(r, s.state, s.label); close();
    }
    function freeText() {
      r.querySelector('[name=medicine_id_' + r.dataset.i + ']').value = '';
      r.querySelector('[name=free_text_' + r.dataset.i + ']').value = input.value.trim();
      r._warn = []; setChip(r, input.value.trim() ? 'red' : '', 'Not in catalog (free text)'); close();
    }
    var run = debounce(function () {
      var q = input.value.trim();
      r.querySelector('[name=medicine_id_' + r.dataset.i + ']').value = '';
      r.querySelector('[name=free_text_' + r.dataset.i + ']').value = q;
      setChip(r, q ? 'red' : '', 'Not in catalog (free text)');
      if (q.length < 2) { close(); return; }
      fetch('/doctor/api/suggest?q=' + encodeURIComponent(q) + '&patient_id=' + cfg.patientId, { credentials: 'same-origin' })
        .then(function (x) { return x.json(); }).then(function (list) {
          close(); if (!Array.isArray(list)) return; items = list; act = -1;
          box = document.createElement('div'); box.className = 'sugg';
          var rc = input.getBoundingClientRect(); box.style.left = (rc.left + window.scrollX) + 'px'; box.style.top = (rc.bottom + window.scrollY) + 'px';
          list.forEach(function (s) {
            var d = document.createElement('div');
            d.innerHTML = '<span class="chip ' + s.state + '">' + esc(s.label) + '</span><span><b>' + esc(s.name) + '</b> ' + esc(s.strength || '') +
              '<br><small class="muted">' + esc(s.generic || '') + (s.allergy_warning && s.allergy_warning.length ? ' · <span class="errtext">allergy: ' + esc(s.allergy_warning.join(', ')) + '</span>' : '') + '</small></span>';
            d.onmousedown = function (e) { e.preventDefault(); pick(s); }; box.appendChild(d);
          });
          var f = document.createElement('div'); f.innerHTML = '<span class="chip red">Not in catalog</span><span>Use "' + esc(q) + '" as free text</span>';
          f.onmousedown = function (e) { e.preventDefault(); freeText(); }; box.appendChild(f);
          document.body.appendChild(box);
        });
    }, 180);
    input.addEventListener('input', run);
    input.addEventListener('blur', function () { setTimeout(close, 150); });
    input.addEventListener('keydown', function (e) {
      if (!box) return; var n = box.children;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); act = (act + (e.key === 'ArrowDown' ? 1 : -1) + n.length) % n.length; [].forEach.call(n, function (c, k) { c.classList.toggle('act', k === act); }); }
      else if (e.key === 'Enter' && act >= 0) { e.preventDefault(); if (act < items.length) pick(items[act]); else freeText(); }
      else if (e.key === 'Escape') close();
    });
  }

  (cfg.existing || []).forEach(function (d) { addRow(d); });
  if (!(cfg.existing || []).length) addRow();
  document.getElementById('addrow').onclick = function () { addRow().querySelector('[data-name]').focus(); };

  // ---- allergy confirmation (R4.4): free-text rows and catalog rows with a warning need an explicit confirmation
  var dlg = document.getElementById('allergydlg');
  form.addEventListener('submit', function (e) {
    if (form.dataset.confirmed) return;
    var need = [];
    rowsBox.querySelectorAll('.rx-row').forEach(function (r) {
      var rm = r.querySelector('[name^=remove_]'); if (rm && rm.checked) return;
      var med = r.querySelector('[name^=medicine_id_]').value, ft = r.querySelector('[name^=free_text_]').value;
      if (!med && !ft) return;
      if (!med) need.push({ r: r, kind: 'free', name: ft });
      else if (r._warn && r._warn.length) need.push({ r: r, kind: 'warn', name: r.querySelector('[data-name]').value, warn: r._warn });
    });
    if (!need.length) return;
    e.preventDefault();
    var list = document.getElementById('dlglist'); list.innerHTML = '';
    need.forEach(function (n, k) {
      var d = document.createElement('div'); d.className = 'card';
      d.innerHTML = '<b>' + esc(n.name) + '</b> ' + (n.kind === 'free' ? '<span class="chip red">free text</span>' : '<span class="chip red">ALLERGY WARNING: ' + esc(n.warn.join(', ')) + '</span>') +
        (n.kind === 'warn' ? '<label class="f"><span>Override reason (required)</span><input data-ovr="' + k + '"></label>' : '');
      list.appendChild(d);
    });
    document.getElementById('dlgallergies').textContent = cfg.allergies.length ? cfg.allergies.join(', ') : 'none recorded';
    dlg.showModal();
    document.getElementById('dlgok').onclick = function (ev) {
      ev.preventDefault();
      if (!document.getElementById('dlgchk').checked) { alert('Tick the box to confirm you have checked the allergies.'); return; }
      for (var k = 0; k < need.length; k++) {
        if (need[k].kind === 'warn') {
          var v = list.querySelector('[data-ovr="' + k + '"]').value.trim();
          if (!v) { alert('Give an override reason for ' + need[k].name); return; }
          need[k].r.querySelector('[name^=override_]').value = v;
        }
        need[k].r.querySelector('[name^=confirm_]').value = '1';
      }
      dlg.close(); form.dataset.confirmed = '1'; form.requestSubmit();
    };
    document.getElementById('dlgcancel').onclick = function () { dlg.close(); };
  });
})();
