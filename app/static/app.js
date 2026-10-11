/* Tiny helpers, no framework. */
(function () {
  // idempotency: one request_uuid per rendered form (G11) so a double click creates one row
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8); return v.toString(16); });
  }
  document.querySelectorAll('input[data-uuid]').forEach(function (i) { i.value = uuid(); });
  // block double submit + confirm prompts
  document.addEventListener('submit', function (e) {
    var f = e.target;
    var btn = e.submitter;
    if (btn && btn.dataset.confirm && !window.confirm(btn.dataset.confirm)) { e.preventDefault(); return; }
    if (f.dataset.busy) { e.preventDefault(); return; }
    if (f.hasAttribute('data-once')) { f.dataset.busy = '1'; setTimeout(function () { delete f.dataset.busy; }, 4000); }
  });
  // auto refresh for boards:  <body data-refresh="30">
  var r = parseInt(document.body.getAttribute('data-refresh') || '0', 10);
  if (r > 0) setInterval(function () { if (!document.querySelector('dialog[open]') && !document.activeElement.matches('input,textarea,select')) location.reload(); }, r * 1000);
  window.csrfToken = function () { var i = document.querySelector('input[name=csrf_token]'); return i ? i.value : ''; };
  window.debounce = function (fn, ms) { var t; return function () { var a = arguments, s = this; clearTimeout(t); t = setTimeout(function () { fn.apply(s, a); }, ms); }; };
})();
