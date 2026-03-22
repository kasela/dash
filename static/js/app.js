(function () {
  'use strict';

  // ── Palettes (mirror of services.py PALETTES) ─────────────────────────────

  var PALETTES = {
    indigo:  ['#6366f1','#8b5cf6','#a78bfa','#c4b5fd','#818cf8','#4f46e5','#7c3aed','#9061f9','#a855f7','#d946ef'],
    blue:    ['#3b82f6','#60a5fa','#93c5fd','#1d4ed8','#2563eb','#0ea5e9','#38bdf8','#7dd3fc','#1e40af','#172554'],
    emerald: ['#10b981','#34d399','#6ee7b7','#059669','#065f46','#14b8a6','#2dd4bf','#5eead4','#0f766e','#134e4a'],
    rose:    ['#f43f5e','#fb7185','#fda4af','#e11d48','#9f1239','#f97316','#fb923c','#fdba74','#ea580c','#7c2d12'],
    amber:   ['#f59e0b','#fbbf24','#fcd34d','#d97706','#92400e','#eab308','#facc15','#fde047','#ca8a04','#713f12'],
    slate:   ['#475569','#64748b','#94a3b8','#1e293b','#334155','#6b7280','#9ca3af','#d1d5db','#374151','#111827'],
    vibrant: ['#6366f1','#10b981','#f59e0b','#f43f5e','#3b82f6','#8b5cf6','#14b8a6','#fb923c','#84cc16','#ec4899'],
    ocean:   ['#0ea5e9','#06b6d4','#22d3ee','#0284c7','#0369a1','#38bdf8','#67e8f9','#0891b2','#155e75','#164e63'],
    sunset:  ['#f97316','#ef4444','#ec4899','#a855f7','#f59e0b','#fb923c','#f43f5e','#d946ef','#e11d48','#9333ea'],
    mono:    ['#1e293b','#334155','#475569','#64748b','#94a3b8','#cbd5e1','#e2e8f0','#334155','#0f172a','#475569'],
    neon:    ['#22d3ee','#a3e635','#fb923c','#f472b6','#c084fc','#34d399','#fbbf24','#f87171','#60a5fa','#4ade80'],
  };

  // ── Utilities ─────────────────────────────────────────────────────────────

  function getCsrfToken() {
    var el = document.getElementById('dashboard-api-urls');
    if (el) {
      try {
        var cfg = JSON.parse(el.textContent);
        if (cfg.csrfToken) return cfg.csrfToken;
      } catch (_) {}
    }
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  function getApiConfig() {
    var el = document.getElementById('dashboard-api-urls');
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (_) { return null; }
  }

  // ── Chart rendering ──────────────────────────────────────────────────────

  // Map widget-id → Chart instance for download / maximize
  var widgetCharts = {};

  function renderHomeChart() {
    var el = document.getElementById('chart-config');
    var canvas = document.getElementById('home-chart');
    if (!el || !canvas || typeof Chart === 'undefined') return;
    try {
      new Chart(canvas, JSON.parse(el.textContent));
    } catch (e) {
      console.warn('DashAI: home chart error', e);
    }
  }

  // Inject live JS tooltip callback (Python cannot serialize JS functions to JSON)
  function _injectTooltipCallback(cfg) {
    try {
      var tooltip = cfg && cfg.options && cfg.options.plugins && cfg.options.plugins.tooltip;
      if (tooltip && tooltip.enabled !== false) {
        if (!tooltip.callbacks) tooltip.callbacks = {};
        if (typeof tooltip.callbacks.label !== 'function') {
          tooltip.callbacks.label = function (ctx) {
            var v = ctx.parsed && ctx.parsed.y !== undefined ? ctx.parsed.y : ctx.parsed;
            if (typeof v === 'number') return ' ' + v.toLocaleString();
            return ' ' + v;
          };
        }
      }
    } catch (_) {}
    return cfg;
  }

  function renderWidgetCharts() {
    document.querySelectorAll('[data-widget-chart]').forEach(function (wrap) {
      var canvas = wrap.querySelector('canvas');
      if (!canvas || typeof Chart === 'undefined') return;
      try {
        var cfg = JSON.parse(wrap.dataset.widgetChart);
        if (!cfg.options) cfg.options = {};
        cfg.options.responsive = true;
        cfg.options.maintainAspectRatio = false;
        _injectTooltipCallback(cfg);
        var widgetId = wrap.dataset.widgetId;
        var chart = new Chart(canvas, cfg);
        if (widgetId) widgetCharts[widgetId] = { chart: chart, config: cfg, canvas: canvas };
      } catch (e) {
        console.warn('DashAI: widget chart error', e);
        var errEl = wrap.querySelector('.chart-error');
        if (errEl) errEl.hidden = false;
      }
    });
  }

  // ── Clipboard copy ────────────────────────────────────────────────────────

  function initCopyButtons() {
    document.querySelectorAll('[data-copy]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var text = btn.dataset.copy;
        navigator.clipboard.writeText(text).then(function () {
          var orig = btn.textContent;
          btn.textContent = 'Copied!';
          btn.classList.add('bg-emerald-600');
          setTimeout(function () {
            btn.textContent = orig;
            btn.classList.remove('bg-emerald-600');
          }, 2000);
        }).catch(function () {
          var input = document.createElement('input');
          input.value = text;
          document.body.appendChild(input);
          input.select();
          document.execCommand('copy');
          document.body.removeChild(input);
        });
      });
    });
  }

  // ── Drag & Drop file upload ───────────────────────────────────────────────

  function initDragDrop() {
    var zone = document.getElementById('drop-zone');
    var input = document.getElementById('dataset-file-input');
    if (!zone || !input) return;

    zone.addEventListener('click', function () { input.click(); });

    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      zone.classList.add('border-indigo-500', 'bg-indigo-50');
    });

    zone.addEventListener('dragleave', function () {
      zone.classList.remove('border-indigo-500', 'bg-indigo-50');
    });

    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('border-indigo-500', 'bg-indigo-50');
      if (e.dataTransfer.files.length > 0) {
        input.files = e.dataTransfer.files;
        updateDropLabel(e.dataTransfer.files[0].name);
      }
    });

    input.addEventListener('change', function () {
      if (input.files.length > 0) updateDropLabel(input.files[0].name);
    });

    function updateDropLabel(name) {
      var label = document.getElementById('drop-zone-label');
      var sub = document.getElementById('drop-zone-sub');
      if (label) label.textContent = name;
      if (sub) sub.textContent = 'File selected – click Parse to continue';
    }
  }

  // ── Mobile sidebar toggle ────────────────────────────────────────────────

  function initSidebar() {
    var toggle = document.getElementById('sidebar-toggle');
    var sidebar = document.getElementById('app-sidebar');
    var overlay = document.getElementById('sidebar-overlay');
    if (!toggle || !sidebar) return;

    toggle.addEventListener('click', function () {
      sidebar.classList.toggle('-translate-x-full');
      if (overlay) overlay.classList.toggle('hidden');
    });

    if (overlay) {
      overlay.addEventListener('click', function () {
        sidebar.classList.add('-translate-x-full');
        overlay.classList.add('hidden');
      });
    }
  }

  // ── Toast notifications ──────────────────────────────────────────────────

  window.showToast = function (message, type) {
    var container = document.getElementById('toast-container');
    if (!container) return;
    var colors = { success: 'bg-emerald-600', error: 'bg-red-600', info: 'bg-indigo-600' };
    var toast = document.createElement('div');
    toast.className = 'flex items-center gap-3 rounded-xl px-4 py-3 text-sm text-white shadow-lg ' + (colors[type] || colors.info);
    toast.innerHTML = '<span class="flex-1">' + message + '</span>' +
      '<button onclick="this.parentElement.remove()" class="ml-2 opacity-70 hover:opacity-100 text-lg leading-none">&times;</button>';
    container.appendChild(toast);
    setTimeout(function () { if (toast.parentElement) toast.remove(); }, 4000);
  };

  // ── Widget deletion ──────────────────────────────────────────────────────

  function doDeleteWidget(btn) {
    var widgetId = btn.dataset.widgetId;
    var deleteUrl = btn.dataset.deleteUrl;
    var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');

    btn.disabled = true;
    btn.classList.add('opacity-50');

    fetch(deleteUrl, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken(), 'Content-Type': 'application/json' },
    })
      .then(function (r) {
        if (!r.ok) throw new Error('Server error ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (data.success) {
          if (card) {
            card.style.transition = 'opacity 0.2s';
            card.style.opacity = '0';
            setTimeout(function () { card.remove(); updateWidgetCount(-1); }, 200);
          }
          showToast('Widget deleted', 'success');
        } else {
          throw new Error(data.error || 'Unknown error');
        }
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.classList.remove('opacity-50');
        showToast('Could not delete widget: ' + err.message, 'error');
      });
  }

  function initWidgetDelete() {
    document.querySelectorAll('.delete-widget-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var widgetId = btn.dataset.widgetId;
        var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');
        if (!card) return;
        if (card.querySelector('.delete-confirm-row')) return;

        var row = document.createElement('div');
        row.className = 'delete-confirm-row mt-3 flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700';
        row.innerHTML =
          '<span class="flex-1">Delete this widget?</span>' +
          '<button class="confirm-yes rounded px-2 py-1 bg-red-600 text-white font-semibold hover:bg-red-700 transition-colors">Delete</button>' +
          '<button class="confirm-no rounded px-2 py-1 bg-white border border-slate-300 text-slate-600 hover:bg-slate-50 transition-colors">Cancel</button>';

        card.appendChild(row);

        row.querySelector('.confirm-yes').addEventListener('click', function () {
          row.remove();
          doDeleteWidget(btn);
        });
        row.querySelector('.confirm-no').addEventListener('click', function () {
          row.remove();
        });
      });
    });
  }

  function updateWidgetCount(delta) {
    var el = document.getElementById('widget-count');
    if (!el) return;
    var match = el.textContent.match(/(\d+)/);
    if (!match) return;
    var n = Math.max(0, parseInt(match[1], 10) + delta);
    el.textContent = n + ' widget' + (n !== 1 ? 's' : '');

    var grid = document.getElementById('widgets-grid');
    var empty = document.getElementById('empty-state');
    if (!grid || !empty) return;
    var remaining = grid.querySelectorAll('.widget-card').length;
    empty.classList.toggle('hidden', remaining > 0);
  }

  // ── Widget Rename ────────────────────────────────────────────────────────

  function initWidgetRename() {
    var cfg = getApiConfig();
    if (!cfg) return;

    document.querySelectorAll('.widget-title').forEach(function (titleEl) {
      titleEl.addEventListener('click', function () {
        var widgetId = titleEl.dataset.widgetId;
        var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');
        if (!card) return;
        var inputEl = card.querySelector('.widget-title-input[data-widget-id="' + widgetId + '"]');
        if (!inputEl) return;

        titleEl.classList.add('hidden');
        inputEl.classList.remove('hidden');
        inputEl.focus();
        inputEl.select();

        function commit() {
          var newTitle = inputEl.value.trim();
          if (!newTitle || newTitle === titleEl.textContent.trim()) {
            cancel();
            return;
          }
          var renameUrl = cfg.renameWidgetBaseUrl + widgetId + '/rename/';
          fetch(renameUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ title: newTitle }),
          })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data.success) {
                titleEl.textContent = data.title;
                showToast('Renamed to "' + data.title + '"', 'success');
              } else {
                showToast(data.error || 'Rename failed', 'error');
                inputEl.value = titleEl.textContent.trim();
              }
              cancel();
            })
            .catch(function (err) {
              showToast('Network error: ' + err.message, 'error');
              cancel();
            });
        }

        function cancel() {
          inputEl.classList.add('hidden');
          titleEl.classList.remove('hidden');
          inputEl.removeEventListener('blur', onBlur);
          inputEl.removeEventListener('keydown', onKey);
        }

        function onBlur() { commit(); }
        function onKey(e) {
          if (e.key === 'Enter') { e.preventDefault(); commit(); }
          if (e.key === 'Escape') { cancel(); }
        }

        inputEl.addEventListener('blur', onBlur);
        inputEl.addEventListener('keydown', onKey);
      });
    });
  }

  // ── Maximize Chart ───────────────────────────────────────────────────────

  var maximizeChart = null;

  function openMaximize(widgetId) {
    var entry = widgetCharts[widgetId];
    if (!entry) return;

    var overlay = document.getElementById('maximize-overlay');
    var modal = document.getElementById('maximize-modal');
    var canvas = document.getElementById('maximize-canvas');
    var titleEl = document.getElementById('maximize-title');
    var insightEl = document.getElementById('maximize-insight-text');
    var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');
    var triggerBtn = document.querySelector('.maximize-widget-btn[data-widget-id="' + widgetId + '"]');

    if (!modal || !canvas) return;

    var title = card ? (card.querySelector('.widget-title') || {}).textContent || '' : '';
    if (titleEl) titleEl.textContent = title;
    if (insightEl) {
      var insight = triggerBtn ? (triggerBtn.dataset.aiInsight || '') : '';
      if (!insight && card) {
        var existingInsightText = card.querySelector('.ai-insights-panel .ai-insights-text');
        insight = existingInsightText ? (existingInsightText.textContent || '').trim() : '';
      }
      insightEl.textContent = insight || 'No insights yet. Click AI Insights on the widget first.';
    }

    modal.style.display = 'flex';
    if (overlay) overlay.style.display = 'block';

    // Destroy previous maximize chart
    if (maximizeChart) { try { maximizeChart.destroy(); } catch (_) {} maximizeChart = null; }

    // Clone config and render
    var cfg = JSON.parse(JSON.stringify(entry.config));
    if (!cfg.options) cfg.options = {};
    cfg.options.responsive = true;
    cfg.options.maintainAspectRatio = false;
    try {
      maximizeChart = new Chart(canvas, cfg);
    } catch (e) {
      console.warn('DashAI: maximize render error', e);
    }

    // Store widgetId for download
    modal.dataset.widgetId = widgetId;
  }

  function closeMaximize() {
    var overlay = document.getElementById('maximize-overlay');
    var modal = document.getElementById('maximize-modal');
    if (overlay) overlay.style.display = 'none';
    if (modal) modal.style.display = 'none';
    if (maximizeChart) { try { maximizeChart.destroy(); } catch (_) {} maximizeChart = null; }
  }

  function initMaximize() {
    document.querySelectorAll('.maximize-widget-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        openMaximize(btn.dataset.widgetId);
      });
    });

    var closeBtn = document.getElementById('close-maximize-btn');
    var overlay = document.getElementById('maximize-overlay');
    if (closeBtn) closeBtn.addEventListener('click', closeMaximize);
    if (overlay) overlay.addEventListener('click', closeMaximize);

    var dlBtn = document.getElementById('maximize-download-btn');
    if (dlBtn) {
      dlBtn.addEventListener('click', function () {
        var modal = document.getElementById('maximize-modal');
        var widgetId = modal ? modal.dataset.widgetId : null;
        downloadChartCanvas(document.getElementById('maximize-canvas'), 'chart-fullscreen');
      });
    }
    var pdfBtn = document.getElementById('maximize-pdf-btn');
    if (pdfBtn) {
      pdfBtn.addEventListener('click', function () {
        var titleEl = document.getElementById('maximize-title');
        var insightEl = document.getElementById('maximize-insight-text');
        downloadChartPdf(
          document.getElementById('maximize-canvas'),
          (titleEl ? titleEl.textContent : '') || 'Dashboard Chart',
          (insightEl ? insightEl.textContent : '') || ''
        );
      });
    }

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        var modal = document.getElementById('maximize-modal');
        if (modal && modal.style.display !== 'none') closeMaximize();
      }
    });
  }

  // ── Download Chart as PNG ─────────────────────────────────────────────────

  // ── PDF / Chart Export ────────────────────────────────────────────────────

  function downloadChartCanvas(canvas, filename) {
    if (!canvas) return;
    var tmpCanvas = document.createElement('canvas');
    tmpCanvas.width = canvas.width;
    tmpCanvas.height = canvas.height;
    var ctx = tmpCanvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, tmpCanvas.width, tmpCanvas.height);
    ctx.drawImage(canvas, 0, 0);
    tmpCanvas.toBlob(function (blob) {
      if (!blob) return;
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = (filename || 'chart') + '.png';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(url); document.body.removeChild(a); }, 1000);
    }, 'image/png');
  }

  function downloadChartPdf(canvas, title, insight) {
    if (!canvas) return;
    var image = canvas.toDataURL('image/png');
    var safeTitle = (title || 'Dashboard Chart').replace(/[<>]/g, '');
    var safeInsight = (insight || '').replace(/[<>]/g, '');
    var ts = new Date().toLocaleString();
    var html = [
      '<!doctype html><html><head><meta charset="utf-8"><title>', safeTitle, '</title>',
      '<style>',
      '@page{size:A4 landscape;margin:12mm}',
      '*{box-sizing:border-box;margin:0;padding:0}',
      'body{font-family:Inter,"Segoe UI",Arial,sans-serif;background:#f8fafc;color:#0f172a;padding:0}',
      '.page{background:#fff;min-height:100vh;padding:24px 28px;display:flex;flex-direction:column;gap:18px}',
      '.header{display:flex;align-items:flex-start;justify-content:space-between;border-bottom:2px solid #e2e8f0;padding-bottom:14px}',
      '.header-left{}',
      '.badge{display:inline-flex;align-items:center;gap:4px;font-size:9px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#6366f1;background:#eef2ff;border:1px solid #c7d2fe;border-radius:99px;padding:3px 8px;margin-bottom:6px}',
      'h1{font-size:20px;font-weight:800;color:#0f172a;line-height:1.2}',
      '.meta-row{font-size:10px;color:#94a3b8;margin-top:4px}',
      '.brand{font-size:11px;font-weight:700;color:#6366f1;letter-spacing:.05em}',
      '.content{display:grid;grid-template-columns:3fr 1fr;gap:16px;flex:1;align-items:start}',
      '.chart-wrap{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}',
      '.chart-wrap img{width:100%;height:auto;display:block}',
      '.insight-wrap{background:linear-gradient(135deg,#f5f3ff,#eef2ff);border:1px solid #c7d2fe;border-radius:14px;padding:14px}',
      '.insight-label{font-size:9px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:#7c3aed;margin-bottom:8px;display:flex;align-items:center;gap:5px}',
      '.insight-dot{width:6px;height:6px;border-radius:50%;background:#7c3aed;display:inline-block}',
      '.insight-text{font-size:11px;color:#1e293b;line-height:1.65;font-weight:450}',
      '.footer{border-top:1px solid #e2e8f0;padding-top:10px;display:flex;justify-content:space-between;align-items:center}',
      '.footer-left{font-size:9px;color:#94a3b8}',
      '.footer-right{font-size:9px;color:#94a3b8}',
      '@media print{body{background:#fff}.page{padding:0}html,body{-webkit-print-color-adjust:exact;print-color-adjust:exact}}',
      '</style></head><body>',
      '<div class="page">',
      '<div class="header">',
      '<div class="header-left">',
      '<div class="badge"><span class="insight-dot"></span>Decision Snapshot</div>',
      '<h1>', safeTitle, '</h1>',
      '<div class="meta-row">Generated ', ts, '</div>',
      '</div>',
      '<div class="brand">DashAI</div>',
      '</div>',
      '<div class="content">',
      '<div class="chart-wrap"><img src="', image, '" alt="', safeTitle, '"></div>',
      '<div class="insight-wrap">',
      '<div class="insight-label"><span class="insight-dot"></span>AI Insight</div>',
      '<p class="insight-text">', (safeInsight || 'No AI insight available for this chart.'), '</p>',
      '</div>',
      '</div>',
      '<div class="footer"><div class="footer-left">Chart: ', safeTitle, '</div><div class="footer-right">DashAI Analytics Platform</div></div>',
      '</div>',
      '</body></html>'
    ].join('');
    printHtmlToPdf(html, safeTitle);
  }

  function initDownloadButtons() {
    document.querySelectorAll('.download-widget-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var widgetId = btn.dataset.widgetId;
        var entry = widgetCharts[widgetId];
        if (!entry) { showToast('No chart to download', 'error'); return; }
        var filename = (btn.dataset.title || 'chart').replace(/[^a-z0-9_\-]/gi, '_').toLowerCase();
        downloadChartCanvas(entry.canvas, filename);
        showToast('Downloading PNG…', 'success');
      });
    });
  }

  // Build a professional PDF HTML document from dashboard data + optional executive summary
  function _buildDashboardPdfHtml(title, kpiBlocks, chartBlocks, tableBlocks, summary, ts) {
    var safeTitle = (title || 'Dashboard').replace(/[<>]/g, '');

    // ── shared CSS ──────────────────────────────────────────────────────
    var css = [
      '@page{size:A4 portrait;margin:12mm}',
      '*{box-sizing:border-box;margin:0;padding:0}',
      'body{font-family:Inter,"Segoe UI",Arial,sans-serif;background:#f1f5f9;color:#0f172a;-webkit-print-color-adjust:exact;print-color-adjust:exact}',
      /* cover */
      '.cover{background:linear-gradient(135deg,#1e1b4b 0%,#312e81 40%,#4338ca 80%,#6366f1 100%);min-height:100vh;display:flex;flex-direction:column;justify-content:space-between;padding:40px 44px;page-break-after:always}',
      '.cover-brand{font-size:13px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:rgba(255,255,255,0.6);margin-bottom:8px}',
      '.cover-badge{display:inline-flex;align-items:center;gap:5px;background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.2);border-radius:99px;padding:4px 12px;font-size:9px;font-weight:700;letter-spacing:.1em;color:rgba(255,255,255,0.8);text-transform:uppercase;margin-bottom:24px}',
      '.cover-title{font-size:34px;font-weight:900;color:#fff;line-height:1.15;letter-spacing:-.02em;max-width:480px}',
      '.cover-subtitle{font-size:14px;color:rgba(255,255,255,0.65);margin-top:12px;line-height:1.5;max-width:440px}',
      '.cover-meta{font-size:11px;color:rgba(255,255,255,0.5)}',
      '.cover-meta strong{color:rgba(255,255,255,0.8)}',
      /* exec summary page */
      '.exec-page{background:#fff;min-height:100vh;padding:32px 36px;page-break-after:always}',
      '.section-label{font-size:9px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:#6366f1;margin-bottom:14px;display:flex;align-items:center;gap:6px}',
      '.section-label::before{content:"";display:inline-block;width:16px;height:2px;background:#6366f1;border-radius:1px}',
      '.exec-headline{font-size:20px;font-weight:800;color:#0f172a;line-height:1.3;margin-bottom:18px;border-left:3px solid #6366f1;padding-left:12px}',
      '.exec-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}',
      '.exec-card{border-radius:12px;padding:16px;border:1px solid #e2e8f0}',
      '.exec-card.findings{background:#f0fdf4;border-color:#bbf7d0}',
      '.exec-card.opportunities{background:#eff6ff;border-color:#bfdbfe}',
      '.exec-card.quality{background:#faf5ff;border-color:#e9d5ff}',
      '.exec-card-title{font-size:9px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px}',
      '.exec-card.findings .exec-card-title{color:#16a34a}',
      '.exec-card.opportunities .exec-card-title{color:#2563eb}',
      '.exec-card.quality .exec-card-title{color:#7c3aed}',
      '.exec-list{list-style:none;display:flex;flex-direction:column;gap:6px}',
      '.exec-list li{font-size:11px;color:#1e293b;line-height:1.5;display:flex;gap:6px;align-items:flex-start}',
      '.exec-list li::before{content:"→";color:#6366f1;font-weight:700;flex-shrink:0;margin-top:1px}',
      '.exec-card.findings .exec-list li::before{color:#16a34a}',
      '.exec-card.opportunities .exec-list li::before{color:#2563eb}',
      /* content pages */
      '.content-page{background:#f8fafc;min-height:100vh;padding:24px 28px;page-break-after:always}',
      '.page-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;padding-bottom:10px;border-bottom:1px solid #e2e8f0}',
      '.page-header-title{font-size:13px;font-weight:700;color:#0f172a}',
      '.page-header-brand{font-size:9px;font-weight:700;color:#6366f1;letter-spacing:.06em;text-transform:uppercase}',
      /* KPI row */
      '.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:20px}',
      '.kpi-card{background:#fff;border-radius:12px;padding:14px;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,.05)}',
      '.kpi-value{font-size:26px;font-weight:900;letter-spacing:-.03em;line-height:1;margin-bottom:4px}',
      '.kpi-value.up{color:#16a34a}.kpi-value.down{color:#dc2626}.kpi-value.flat{color:#6366f1}',
      '.kpi-label{font-size:9px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#94a3b8}',
      '.kpi-trend{font-size:10px;font-weight:600;margin-top:6px}',
      '.kpi-trend.up{color:#16a34a}.kpi-trend.down{color:#dc2626}.kpi-trend.flat{color:#64748b}',
      /* chart grid */
      '.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}',
      '.chart-grid.single{grid-template-columns:1fr}',
      '.chart-card{background:#fff;border-radius:12px;border:1px solid #e2e8f0;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.05);break-inside:avoid;page-break-inside:avoid}',
      '.chart-card.full{grid-column:1/-1}',
      '.chart-title{font-size:12px;font-weight:700;color:#0f172a;margin-bottom:10px}',
      '.chart-img{width:100%;height:auto;display:block;border-radius:6px}',
      '.chart-insight{margin-top:10px;background:linear-gradient(135deg,#f5f3ff,#eef2ff);border:1px solid #c7d2fe;border-radius:8px;padding:10px}',
      '.chart-insight-label{font-size:8px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#7c3aed;margin-bottom:5px}',
      '.chart-insight-text{font-size:10px;color:#1e293b;line-height:1.6}',
      /* tables */
      '.table-card{background:#fff;border-radius:12px;border:1px solid #e2e8f0;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.05);margin-top:14px}',
      'table{width:100%;border-collapse:collapse}',
      'th{background:#f8fafc;font-size:9px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#475569;padding:7px 10px;text-align:left;border-bottom:2px solid #e2e8f0}',
      'td{font-size:10px;color:#1e293b;padding:6px 10px;border-bottom:1px solid #f1f5f9}',
      'tr:last-child td{border-bottom:none}',
      'tr:hover td{background:#f8fafc}',
      /* footer */
      '.pdf-footer{position:fixed;bottom:6mm;left:0;right:0;padding:0 12mm;display:flex;justify-content:space-between;align-items:center;font-size:8px;color:#94a3b8}',
      '@media print{body{background:#fff}.cover,.exec-page,.content-page{min-height:auto}}'
    ].join('');

    // ── Cover page ──────────────────────────────────────────────────────
    var widgetCountEl = document.getElementById('widget-count');
    var widgetCount = widgetCountEl ? widgetCountEl.textContent.trim() : '';
    var coverHtml = [
      '<div class="cover">',
      '<div>',
      '<div class="cover-brand">DashAI Analytics</div>',
      '<div class="cover-badge">&#9670; Executive Report</div>',
      '<div class="cover-title">', safeTitle, '</div>',
      '<div class="cover-subtitle">A comprehensive data-driven report generated from your business dashboard with AI-powered analysis and key insights.</div>',
      '</div>',
      '<div class="cover-meta">',
      '<div><strong>Generated:</strong> ', ts, '</div>',
      (widgetCount ? '<div style="margin-top:4px"><strong>Widgets:</strong> ' + widgetCount + '</div>' : ''),
      '</div>',
      '</div>'
    ].join('');

    // ── Executive Summary page (if available) ───────────────────────────
    var execHtml = '';
    if (summary && (summary.headline || summary.findings)) {
      var findingsHtml = (summary.findings || []).map(function (f) {
        return '<li>' + String(f).replace(/[<>]/g, '') + '</li>';
      }).join('');
      var oppsHtml = (summary.opportunities || []).map(function (o) {
        return '<li>' + String(o).replace(/[<>]/g, '') + '</li>';
      }).join('');
      execHtml = [
        '<div class="exec-page">',
        '<div class="section-label">Executive Summary</div>',
        '<div class="exec-headline">', String(summary.headline || '').replace(/[<>]/g, ''), '</div>',
        '<div class="exec-grid">',
        '<div class="exec-card findings">',
        '<div class="exec-card-title">Key Findings</div>',
        '<ul class="exec-list">', findingsHtml, '</ul>',
        '</div>',
        '<div class="exec-card opportunities">',
        '<div class="exec-card-title">Recommended Actions</div>',
        '<ul class="exec-list">', oppsHtml, '</ul>',
        '</div>',
        '</div>',
        (summary.data_quality ? [
          '<div class="exec-card quality" style="grid-column:unset">',
          '<div class="exec-card-title">Data Quality</div>',
          '<p style="font-size:11px;color:#1e293b;line-height:1.5">', String(summary.data_quality).replace(/[<>]/g, ''), '</p>',
          '</div>'
        ].join('') : ''),
        '</div>'
      ].join('');
    }

    // ── KPI section ─────────────────────────────────────────────────────
    var kpiSectionHtml = '';
    if (kpiBlocks.length > 0) {
      kpiSectionHtml = '<div class="kpi-row">' + kpiBlocks.join('') + '</div>';
    }

    // ── Chart grid ──────────────────────────────────────────────────────
    var chartSectionHtml = '';
    if (chartBlocks.length > 0) {
      chartSectionHtml = '<div class="chart-grid">' + chartBlocks.join('') + '</div>';
    }

    // ── Tables ──────────────────────────────────────────────────────────
    var tableSectionHtml = tableBlocks.join('');

    // ── Content page ─────────────────────────────────────────────────────
    var contentHtml = [
      '<div class="content-page">',
      '<div class="page-header">',
      '<div class="page-header-title">', safeTitle, ' — Dashboard Analysis</div>',
      '<div class="page-header-brand">DashAI</div>',
      '</div>',
      kpiSectionHtml,
      chartSectionHtml,
      tableSectionHtml,
      '</div>'
    ].join('');

    return [
      '<!doctype html><html><head><meta charset="utf-8"><title>', safeTitle, '</title>',
      '<style>', css, '</style>',
      '</head><body>',
      coverHtml,
      execHtml,
      contentHtml,
      '<div class="pdf-footer"><span>DashAI Analytics Platform</span><span>', safeTitle, ' · ', ts, '</span></div>',
      '</body></html>'
    ].join('');
  }

  function exportDashboardPdf() {
    var titleEl = document.getElementById('dashboard-title');
    var title = titleEl ? titleEl.textContent.trim() : 'Dashboard';
    var cfg = getApiConfig();

    var kpiBlocks = [];
    var chartBlocks = [];
    var tableBlocks = [];

    // Collect widget data from DOM
    document.querySelectorAll('.widget-card').forEach(function (card) {
      var widgetType = card.dataset.widgetType;
      var widgetId = card.dataset.widgetId;

      // Skip structural widgets
      if (widgetType === 'divider') return;
      if (widgetType === 'heading') return;

      var titleEl2 = card.querySelector('.widget-title');
      var widgetTitle = titleEl2 ? titleEl2.textContent.trim() : '';
      var insightEl = card.querySelector('.ai-insights-text');
      var insight = insightEl ? insightEl.textContent.trim() : '';
      if (insight === 'Analysing data…' || insight === 'Analysing…') insight = '';

      if (widgetType === 'kpi') {
        // Build KPI card HTML
        var valEl = card.querySelector('.widget-kpi-value');
        var labelEl = card.querySelector('.widget-kpi-value + span, .widget-kpi-value ~ span');
        var kpiValue = valEl ? valEl.textContent.trim() : '–';
        var kpiLabel = widgetTitle;
        var trendDir = 'flat';
        if (valEl) {
          if (valEl.classList.contains('text-emerald-600')) trendDir = 'up';
          else if (valEl.classList.contains('text-rose-600')) trendDir = 'down';
        }
        var trendBadge = card.querySelector('.rounded-full.text-xs.font-bold');
        var trendText = trendBadge ? trendBadge.textContent.trim() : '';

        kpiBlocks.push([
          '<div class="kpi-card">',
          '<div class="kpi-value ', trendDir, '">', kpiValue.replace(/[<>]/g, ''), '</div>',
          '<div class="kpi-label">', kpiLabel.replace(/[<>]/g, ''), '</div>',
          (trendText ? '<div class="kpi-trend ' + trendDir + '">' + (trendDir === 'up' ? '▲' : trendDir === 'down' ? '▼' : '—') + ' ' + trendText.replace(/[<>]/g, '') + '</div>' : ''),
          '</div>'
        ].join(''));
        return;
      }

      if (widgetType === 'table') {
        // Clone table
        var tableWrap = card.querySelector('.widget-table-wrap');
        if (tableWrap) {
          var tableClone = tableWrap.cloneNode(true);
          tableClone.querySelectorAll('.table-search-input').forEach(function (el) { el.remove(); });
          tableBlocks.push([
            '<div class="table-card">',
            '<div class="chart-title">', widgetTitle.replace(/[<>]/g, ''), '</div>',
            tableClone.innerHTML,
            (insight ? '<div class="chart-insight" style="margin-top:8px"><div class="chart-insight-label">AI Insight</div><p class="chart-insight-text">' + insight.replace(/[<>]/g, '') + '</p></div>' : ''),
            '</div>'
          ].join(''));
        }
        return;
      }

      // Chart widgets
      var entry = widgetCharts[widgetId];
      var imgSrc = '';
      if (entry && entry.canvas) {
        try {
          var tmpC = document.createElement('canvas');
          tmpC.width = entry.canvas.width;
          tmpC.height = entry.canvas.height;
          var tCtx = tmpC.getContext('2d');
          tCtx.fillStyle = '#ffffff';
          tCtx.fillRect(0, 0, tmpC.width, tmpC.height);
          tCtx.drawImage(entry.canvas, 0, 0);
          imgSrc = tmpC.toDataURL('image/png');
        } catch (_) {}
      }
      if (!imgSrc) return;

      // Is this a full-width (lg) widget?
      var isLg = card.classList.contains('sm:col-span-2') || card.dataset.widgetSize === 'lg';
      var cardClass = 'chart-card' + (isLg ? ' full' : '');

      chartBlocks.push([
        '<div class="', cardClass, '">',
        '<div class="chart-title">', widgetTitle.replace(/[<>]/g, ''), '</div>',
        '<img class="chart-img" src="', imgSrc, '" alt="', widgetTitle.replace(/[<>]/g, ''), '">',
        (insight ? [
          '<div class="chart-insight">',
          '<div class="chart-insight-label">&#9670; AI Insight</div>',
          '<p class="chart-insight-text">', insight.replace(/[<>]/g, ''), '</p>',
          '</div>'
        ].join('') : ''),
        '</div>'
      ].join(''));
    });

    var ts = new Date().toLocaleString();

    // Try to fetch executive summary from AI, then build PDF
    if (cfg && cfg.executiveSummaryUrl && cfg.csrfToken) {
      showToast('Generating AI executive summary…', 'info');
      fetch(cfg.executiveSummaryUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': cfg.csrfToken, 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var summary = (data.success && data.summary) ? data.summary : null;
          var html = _buildDashboardPdfHtml(title, kpiBlocks, chartBlocks, tableBlocks, summary, ts);
          printHtmlToPdf(html, title);
        })
        .catch(function () {
          var html = _buildDashboardPdfHtml(title, kpiBlocks, chartBlocks, tableBlocks, null, ts);
          printHtmlToPdf(html, title);
        });
    } else {
      var html = _buildDashboardPdfHtml(title, kpiBlocks, chartBlocks, tableBlocks, null, ts);
      printHtmlToPdf(html, title);
    }
  }

  function printHtmlToPdf(html, title) {
    var frame = document.createElement('iframe');
    frame.style.position = 'fixed';
    frame.style.right = '0';
    frame.style.bottom = '0';
    frame.style.width = '0';
    frame.style.height = '0';
    frame.style.border = '0';
    frame.setAttribute('aria-hidden', 'true');
    document.body.appendChild(frame);

    var doc = frame.contentWindow.document;
    doc.open();
    doc.write(html);
    doc.close();

    setTimeout(function () {
      try {
        frame.contentWindow.focus();
        frame.contentWindow.print();
      } catch (_) {
        showToast('Unable to open print dialog for PDF export.', 'error');
      } finally {
        setTimeout(function () {
          try { document.body.removeChild(frame); } catch (_) {}
        }, 2000);
      }
    }, 300);
    showToast((title || 'PDF') + ' export ready — opening print dialog.', 'success');
  }

  function initDashboardPdfExport() {
    var btn = document.getElementById('export-dashboard-pdf-btn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      exportDashboardPdf();
    });
  }

  // ── Palette dot rendering ─────────────────────────────────────────────────

  function renderPaletteDots() {
    document.querySelectorAll('.palette-dots').forEach(function (el) {
      var pname = el.dataset.palette;
      var colors = PALETTES[pname] || PALETTES.indigo;
      el.innerHTML = '';
      colors.slice(0, 4).forEach(function (color) {
        var dot = document.createElement('span');
        dot.style.cssText = 'display:inline-block;width:10px;height:10px;border-radius:50%;background:' + color;
        el.appendChild(dot);
      });
    });
  }

  // ── Chart Builder Modal ──────────────────────────────────────────────────

  var cbPreviewChart = null;
  var cbEditingWidgetId = null;
  var cbPendingEdit = null;

  var AXIS_TYPES = new Set(['bar', 'line', 'area', 'hbar', 'scatter', 'map', 'radar', 'bubble', 'mixed', 'waterfall', 'funnel']);
  var MULTI_MEASURE_TYPES = new Set(['bar', 'line', 'mixed']);
  var SCATTER_TYPES = new Set(['scatter', 'map', 'bubble']);
  var DIMENSION_TYPES = new Set(['bar', 'line', 'area', 'pie', 'doughnut', 'hbar', 'radar', 'table', 'polararea', 'funnel', 'waterfall', 'mixed']);
  var MEASURE_TYPES = new Set(['bar', 'line', 'area', 'hbar', 'radar', 'kpi', 'pie', 'table', 'polararea', 'funnel', 'gauge', 'waterfall', 'mixed']);
  var PRO_TYPES = new Set(['bubble', 'polararea', 'mixed', 'funnel', 'gauge', 'waterfall']);

  function openChartBuilder(opts) {
    cbPendingEdit = opts || null;
    var modal = document.getElementById('chart-builder-modal');
    var overlay = document.getElementById('chart-builder-overlay');
    if (!modal) return;

    modal.style.display = 'flex';
    if (overlay) overlay.style.display = 'block';

    document.getElementById('cb-loading').style.display = 'flex';
    document.getElementById('cb-error').style.display = 'none';
    document.getElementById('cb-form').style.display = 'none';
    document.getElementById('cb-preview-btn').style.display = 'none';
    document.getElementById('cb-submit-btn').style.display = 'none';
    document.getElementById('cb-preview-wrap').style.display = 'none';
    var valErr = document.getElementById('cb-validation-error');
    if (valErr) valErr.style.display = 'none';
    cbEditingWidgetId = cbPendingEdit ? cbPendingEdit.widgetId : null;
    var submitBtn = document.getElementById('cb-submit-btn');
    if (submitBtn) submitBtn.textContent = cbEditingWidgetId ? 'Save Widget' : 'Add to Dashboard';

    var titleInput = document.getElementById('cb-title');
    if (titleInput) titleInput.value = '';
    destroyCbPreview();

    var cfg = getApiConfig();
    if (!cfg || !cfg.columnsUrl) {
      showCbError('Configuration error: dashboard API URLs not found.');
      return;
    }

    var versionId = getSelectedDatasetVersionId();
    var fetchUrl = cfg.columnsUrl + (versionId ? '?version_id=' + encodeURIComponent(versionId) : '');
    fetch(fetchUrl, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('Server returned ' + r.status);
        return r.json();
      })
      .then(function (data) {
        populateCbForm(data);
        document.getElementById('cb-loading').style.display = 'none';
        document.getElementById('cb-form').style.display = 'block';
        document.getElementById('cb-preview-btn').style.display = '';
        document.getElementById('cb-submit-btn').style.display = '';
        if (cbPendingEdit && cbPendingEdit.type) {
          var typeRadio = document.querySelector('input[name="cb_chart_type"][value="' + cbPendingEdit.type + '"]');
          if (typeRadio) typeRadio.checked = true;
        }
        updateCbFieldVisibility(!cbPendingEdit);
        applyPendingSelections();
        if (cbPendingEdit && cbPendingEdit.title) {
          var t = document.getElementById('cb-title');
          if (t) t.value = cbPendingEdit.title;
        }
      })
      .catch(function (err) {
        showCbError('Could not load dataset columns (' + err.message + '). Make sure the dataset file is still accessible.');
      });
  }

  function closeChartBuilder() {
    var modal = document.getElementById('chart-builder-modal');
    var overlay = document.getElementById('chart-builder-overlay');
    if (modal) modal.style.display = 'none';
    if (overlay) overlay.style.display = 'none';
    destroyCbPreview();
    cbPendingEdit = null;
  }

  function showCbError(msg) {
    document.getElementById('cb-loading').style.display = 'none';
    var el = document.getElementById('cb-error');
    el.textContent = msg;
    el.style.display = 'block';
  }

  function populateCbForm(data) {
    var dimensions = Array.isArray(data.dimensions) ? data.dimensions : [];
    var measures = Array.isArray(data.measures) ? data.measures : [];
    var allCols = Array.isArray(data.all_cols) ? data.all_cols : [];

    var dimSel = document.getElementById('cb-dimension');
    var measureSel = document.getElementById('cb-measure');
    var measuresSel = document.getElementById('cb-measures');
    var xMeasureSel = document.getElementById('cb-x-measure');
    var yMeasureSel = document.getElementById('cb-y-measure');
    var tableColsSel = document.getElementById('cb-table-columns');
    var groupBySel = document.getElementById('cb-group-by');

    dimSel.innerHTML = '<option value="">— select column —</option>';
    measureSel.innerHTML = '<option value="">— select column —</option>';
    if (measuresSel) measuresSel.innerHTML = '';
    if (xMeasureSel) xMeasureSel.innerHTML = '<option value="">— select column —</option>';
    if (yMeasureSel) yMeasureSel.innerHTML = '<option value="">— select column —</option>';
    if (tableColsSel) tableColsSel.innerHTML = '';
    if (groupBySel) groupBySel.innerHTML = '';

    var dimCols = dimensions.length > 0 ? dimensions : allCols;
    dimCols.forEach(function (col) {
      var opt = document.createElement('option');
      opt.value = col; opt.textContent = col;
      dimSel.appendChild(opt);
      if (groupBySel) {
        var optGroup = document.createElement('option');
        optGroup.value = col; optGroup.textContent = col;
        groupBySel.appendChild(optGroup);
      }
    });

    measures.forEach(function (col) {
      var opt1 = document.createElement('option'); opt1.value = col; opt1.textContent = col;
      measureSel.appendChild(opt1);

      if (measuresSel) {
        var opt2 = document.createElement('option'); opt2.value = col; opt2.textContent = col;
        measuresSel.appendChild(opt2);
      }
      if (xMeasureSel) {
        var opt3 = document.createElement('option'); opt3.value = col; opt3.textContent = col;
        xMeasureSel.appendChild(opt3);
      }
      if (yMeasureSel) {
        var opt4 = document.createElement('option'); opt4.value = col; opt4.textContent = col;
        yMeasureSel.appendChild(opt4);
      }
    });

    allCols.forEach(function (col) {
      if (tableColsSel) {
        var opt = document.createElement('option');
        opt.value = col; opt.textContent = col;
        tableColsSel.appendChild(opt);
      }
      if (groupBySel && dimensions.indexOf(col) === -1) {
        var optGroup = document.createElement('option');
        optGroup.value = col; optGroup.textContent = col;
        groupBySel.appendChild(optGroup);
      }
    });

    if (dimCols.length > 0) dimSel.value = dimCols[0];
    if (measures.length > 0) {
      measureSel.value = measures[0];
      if (xMeasureSel && measures.length > 0) xMeasureSel.value = measures[0];
      if (yMeasureSel && measures.length > 1) yMeasureSel.value = measures[1];
    }

    autoSetTitle();

    if (dimCols.length === 0 && measures.length === 0) {
      showCbValidationError('No columns found in this dataset. Try re-uploading the file.');
    }
  }

  function setMultiSelectValues(selectEl, values) {
    if (!selectEl || !Array.isArray(values)) return;
    var wanted = new Set(values);
    for (var i = 0; i < selectEl.options.length; i++) {
      selectEl.options[i].selected = wanted.has(selectEl.options[i].value);
    }
  }

  function applyPendingSelections() {
    if (!cbPendingEdit || !cbPendingEdit.config || !cbPendingEdit.config.builder) return;
    var builder = cbPendingEdit.config.builder || {};
    var dimEl = document.getElementById('cb-dimension');
    var measureEl = document.getElementById('cb-measure');
    var xMeasureEl = document.getElementById('cb-x-measure');
    var yMeasureEl = document.getElementById('cb-y-measure');
    var xLabelEl = document.getElementById('cb-x-label');
    var yLabelEl = document.getElementById('cb-y-label');
    if (dimEl && builder.dimension) dimEl.value = builder.dimension;
    if (measureEl && builder.measure) measureEl.value = builder.measure;
    if (xMeasureEl && builder.x_measure) xMeasureEl.value = builder.x_measure;
    if (yMeasureEl && builder.y_measure) yMeasureEl.value = builder.y_measure;
    if (xLabelEl && typeof builder.x_label === 'string') xLabelEl.value = builder.x_label;
    if (yLabelEl && typeof builder.y_label === 'string') yLabelEl.value = builder.y_label;
    if (Array.isArray(builder.measures)) setMultiSelectValues(document.getElementById('cb-measures'), builder.measures);
    if (Array.isArray(builder.table_columns)) setMultiSelectValues(document.getElementById('cb-table-columns'), builder.table_columns);
    if (Array.isArray(builder.group_by)) setMultiSelectValues(document.getElementById('cb-group-by'), builder.group_by);
    var aiPromptEl = document.getElementById('cb-ai-prompt');
    if (aiPromptEl && typeof builder.ai_prompt === 'string') {
      aiPromptEl.value = builder.ai_prompt;
      updateAiPromptCounter();
    }
    if (builder.palette) {
      var paletteInput = document.querySelector('input[name="cb_palette"][value="' + builder.palette + '"]');
      if (paletteInput) {
        // Uncheck all palette radios first, then check the right one
        document.querySelectorAll('input[name="cb_palette"]').forEach(function (r) { r.checked = false; });
        paletteInput.checked = true;
        // Update visual border/background classes manually for browsers that don't re-evaluate has-[:checked]
        document.querySelectorAll('.cb-palette-option').forEach(function (lbl) {
          var inp = lbl.querySelector('input[name="cb_palette"]');
          var span = lbl.querySelector('span');
          if (!inp || !span) return;
          if (inp.checked) {
            span.style.borderColor = '#6d28d9';
            span.style.backgroundColor = '#f5f3ff';
          } else {
            span.style.borderColor = '';
            span.style.backgroundColor = '';
          }
        });
      }
    }
    // Restore tooltip toggle — read from builder metadata (most reliable)
    var tooltipChk = document.getElementById('cb-tooltip-enabled');
    if (tooltipChk) {
      var tooltipOn = builder.tooltip_enabled !== false;
      tooltipChk.checked = tooltipOn;
    }
  }

  function getSelectedChartType() {
    var checked = document.querySelector('input[name="cb_chart_type"]:checked');
    return checked ? checked.value : 'bar';
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function getSelectedPalette() {
    var checked = document.querySelector('input[name="cb_palette"]:checked');
    return checked ? checked.value : 'indigo';
  }

  function autoSetTitle() {
    var titleInput = document.getElementById('cb-title');
    if (!titleInput || titleInput.value.trim()) return;
    var type = getSelectedChartType();
    var dim = (document.getElementById('cb-dimension') || {}).value || '';
    var measure = (document.getElementById('cb-measure') || {}).value || '';
    var xm = (document.getElementById('cb-x-measure') || {}).value || '';
    var ym = (document.getElementById('cb-y-measure') || {}).value || '';
    var aiPrompt = (document.getElementById('cb-ai-prompt') || {}).value || '';
    if (type === 'kpi' && measure) titleInput.value = 'Total ' + measure;
    else if (type === 'smart' && aiPrompt.trim()) titleInput.value = aiPrompt.trim().slice(0, 60);
    else if (type === 'smart') titleInput.value = 'AI Smart Analysis';
    else if (type === 'pie' && dim) titleInput.value = 'Distribution: ' + dim;
    else if (type === 'doughnut' && dim) titleInput.value = 'Breakdown: ' + dim;
    else if ((type === 'scatter' || type === 'map' || type === 'bubble') && xm && ym) titleInput.value = xm + ' vs ' + ym;
    else if (type === 'radar' && dim) titleInput.value = dim + ' Radar';
    else if (type === 'polararea' && dim) titleInput.value = 'Polar: ' + dim;
    else if (type === 'funnel' && dim) titleInput.value = dim + ' Funnel';
    else if (type === 'gauge' && measure) titleInput.value = measure + ' Gauge';
    else if (type === 'waterfall' && dim) titleInput.value = dim + ' Waterfall';
    else if (type === 'mixed' && dim) titleInput.value = dim + ' Overview';
    else if (dim && measure) titleInput.value = measure + ' by ' + dim;
  }

  function updateCbFieldVisibility(shouldResetTitle) {
    var type = getSelectedChartType();
    var dimWrap = document.getElementById('cb-dimension-wrap');
    var measureWrap = document.getElementById('cb-measure-wrap');
    var measuresWrap = document.getElementById('cb-measures-wrap');
    var xMeasureWrap = document.getElementById('cb-x-measure-wrap');
    var yMeasureWrap = document.getElementById('cb-y-measure-wrap');
    var axisWrap = document.getElementById('cb-axis-labels-wrap');
    var tableColsWrap = document.getElementById('cb-table-columns-wrap');
    var groupByWrap = document.getElementById('cb-group-by-wrap');
    var promptWrap = document.getElementById('cb-ai-prompt-wrap');

    var showDim = DIMENSION_TYPES.has(type);
    var showMeasure = MEASURE_TYPES.has(type) && !SCATTER_TYPES.has(type);
    var showMulti = MULTI_MEASURE_TYPES.has(type);
    var showScatter = SCATTER_TYPES.has(type);
    var showAxis = AXIS_TYPES.has(type);

    if (dimWrap) dimWrap.style.display = showDim ? '' : 'none';
    if (measureWrap) measureWrap.style.display = (showMeasure && !showMulti) ? '' : 'none';
    if (measuresWrap) measuresWrap.style.display = showMulti ? '' : 'none';
    if (xMeasureWrap) xMeasureWrap.style.display = showScatter ? '' : 'none';
    if (yMeasureWrap) yMeasureWrap.style.display = showScatter ? '' : 'none';
    if (axisWrap) axisWrap.style.display = showAxis ? '' : 'none';
    if (tableColsWrap) tableColsWrap.style.display = type === 'table' ? '' : 'none';
    if (groupByWrap) groupByWrap.style.display = type === 'table' ? '' : 'none';
    if (promptWrap) promptWrap.style.display = type === 'smart' ? '' : 'none';

    // Update selected visual state on type pills
    document.querySelectorAll('.cb-type-option').forEach(function (lbl) {
      var radio = lbl.querySelector('input[type="radio"]');
      if (radio && radio.checked) {
        lbl.style.borderColor = '#4f46e5';
        lbl.style.backgroundColor = '#eef2ff';
      } else {
        lbl.style.borderColor = '';
        lbl.style.backgroundColor = '';
      }
    });

    // Re-apply palette visual state (changing chart type must not reset palette visual)
    document.querySelectorAll('.cb-palette-option').forEach(function (lbl) {
      var inp = lbl.querySelector('input[name="cb_palette"]');
      var span = lbl.querySelector('span');
      if (!inp || !span) return;
      if (inp.checked) {
        span.style.borderColor = '#6d28d9';
        span.style.backgroundColor = '#f5f3ff';
      } else {
        span.style.borderColor = '';
        span.style.backgroundColor = '';
      }
    });

    if (shouldResetTitle !== false) {
      document.getElementById('cb-title').value = '';
      autoSetTitle();
    }
    destroyCbPreview();
    document.getElementById('cb-preview-wrap').style.display = 'none';
    hideCbValidationError();
  }

  function showCbValidationError(msg) {
    var el = document.getElementById('cb-validation-error');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
  }

  function hideCbValidationError() {
    var el = document.getElementById('cb-validation-error');
    if (el) el.style.display = 'none';
  }

  function getSelectedMeasures() {
    var type = getSelectedChartType();
    if (MULTI_MEASURE_TYPES.has(type)) {
      var sel = document.getElementById('cb-measures');
      if (sel) {
        var selected = [];
        for (var i = 0; i < sel.options.length; i++) {
          if (sel.options[i].selected) selected.push(sel.options[i].value);
        }
        if (selected.length > 0) return selected;
      }
    }
    // Fallback: single measure
    var single = (document.getElementById('cb-measure') || {}).value || '';
    return single ? [single] : [];
  }

  function getSelectedMultiValues(id) {
    var sel = document.getElementById(id);
    if (!sel) return [];
    var selected = [];
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].selected) selected.push(sel.options[i].value);
    }
    return selected;
  }

  function updateAiPromptCounter() {
    var input = document.getElementById('cb-ai-prompt');
    var count = document.getElementById('cb-ai-prompt-count');
    if (!input || !count) return;
    count.textContent = String(input.value.length) + ' / 600';
  }

  function validateCbForm() {
    var type = getSelectedChartType();
    var dim = (document.getElementById('cb-dimension') || {}).value || '';
    var measures = getSelectedMeasures();
    var measure = measures[0] || '';
    var xm = (document.getElementById('cb-x-measure') || {}).value || '';
    var ym = (document.getElementById('cb-y-measure') || {}).value || '';
    var aiPrompt = ((document.getElementById('cb-ai-prompt') || {}).value || '').trim();

    if (type === 'smart' && aiPrompt.length > 0 && aiPrompt.length < 16) {
      showCbValidationError('For Smart AI, use a more specific prompt (at least 16 characters).');
      return false;
    }

    if (type === 'kpi' && !measure) {
      showCbValidationError('Select a measure column for the KPI.');
      return false;
    }
    if ((type === 'bar' || type === 'line' || type === 'mixed') && (!dim || measures.length === 0)) {
      showCbValidationError('Select a dimension and at least one measure column.');
      return false;
    }
    if ((type === 'area' || type === 'hbar' || type === 'radar' || type === 'funnel' || type === 'waterfall') && (!dim || !measure)) {
      showCbValidationError('Select both a dimension and a measure column.');
      return false;
    }
    if ((type === 'pie' || type === 'doughnut' || type === 'polararea') && !dim) {
      showCbValidationError('Select a dimension column for this chart.');
      return false;
    }
    if ((type === 'scatter' || type === 'map' || type === 'bubble') && (!xm || !ym)) {
      showCbValidationError('Select both X and Y numeric columns for the scatter/map/bubble chart.');
      return false;
    }
    if (type === 'gauge' && !measure) {
      showCbValidationError('Select a measure column for the gauge.');
      return false;
    }
    if (type === 'table' && !dim && measures.length === 0) {
      showCbValidationError('Select at least one dimension or measure column for the table.');
      return false;
    }
    if (type === 'table') {
      var tableColumns = getSelectedMultiValues('cb-table-columns');
      if (tableColumns.length === 0 && !dim && measures.length === 0) {
        showCbValidationError('Select at least one table column, dimension, or measure.');
        return false;
      }
    }
    hideCbValidationError();
    return true;
  }

  function destroyCbPreview() {
    if (cbPreviewChart) {
      try { cbPreviewChart.destroy(); } catch (_) {}
      cbPreviewChart = null;
    }
    var canvas = document.getElementById('cb-preview-canvas');
    if (canvas) {
      try { canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height); } catch (_) {}
    }
    var kpiEl = document.getElementById('cb-preview-kpi');
    if (kpiEl) { kpiEl.style.display = 'none'; kpiEl.textContent = ''; }
    var tableEl = document.getElementById('cb-preview-table');
    if (tableEl) { tableEl.style.display = 'none'; tableEl.innerHTML = ''; }
  }

  function getSelectedDatasetVersionId() {
    var sel = document.getElementById('cb-dataset');
    return sel ? (sel.value || '') : '';
  }

  function buildPayload(previewOnly) {
    var type = getSelectedChartType();
    var measures = getSelectedMeasures();
    var payload = {
      chart_type: type,
      title: (document.getElementById('cb-title').value || '').trim() || 'New Widget',
      dimension: (document.getElementById('cb-dimension') || {}).value || '',
      measures: measures,
      measure: measures[0] || '',
      x_measure: (document.getElementById('cb-x-measure') || {}).value || '',
      y_measure: (document.getElementById('cb-y-measure') || {}).value || '',
      x_label: (document.getElementById('cb-x-label') || {}).value || '',
      y_label: (document.getElementById('cb-y-label') || {}).value || '',
      table_columns: getSelectedMultiValues('cb-table-columns'),
      group_by: getSelectedMultiValues('cb-group-by'),
      palette: getSelectedPalette(),
      tooltip_enabled: (document.getElementById('cb-tooltip-enabled') || { checked: true }).checked,
      ai_prompt: (document.getElementById('cb-ai-prompt') || {}).value || '',
      preview_only: !!previewOnly,
    };
    var versionId = getSelectedDatasetVersionId();
    if (versionId) payload.dataset_version_id = parseInt(versionId, 10);
    return payload;
  }

  function previewChart() {
    if (!validateCbForm()) return;

    var cfg = getApiConfig();
    if (!cfg) return;

    var previewBtn = document.getElementById('cb-preview-btn');
    previewBtn.textContent = 'Loading…';
    previewBtn.disabled = true;

    fetch(cfg.addWidgetUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify(buildPayload(true)),
    })
      .then(function (r) {
        if (!r.ok) throw new Error('Server error ' + r.status);
        return r.json();
      })
      .then(function (data) {
        previewBtn.textContent = 'Preview';
        previewBtn.disabled = false;
        if (data.error) { showCbValidationError(data.error); return; }
        renderCbPreview(data.chart_config, getSelectedChartType());
      })
      .catch(function (err) {
        previewBtn.textContent = 'Preview';
        previewBtn.disabled = false;
        showCbValidationError('Preview failed: ' + err.message);
      });
  }

  function renderCbPreview(config, type) {
    destroyCbPreview();

    var previewWrap = document.getElementById('cb-preview-wrap');
    var kpiEl = document.getElementById('cb-preview-kpi');
    var canvas = document.getElementById('cb-preview-canvas');

    previewWrap.style.display = 'block';

    if (type === 'kpi') {
      canvas.style.display = 'none';
      kpiEl.style.display = 'block';
      kpiEl.textContent = (config && config.value) ? config.value : '–';
      return;
    }
    var tableEl = document.getElementById('cb-preview-table');
    if (type === 'table') {
      canvas.style.display = 'none';
      if (kpiEl) kpiEl.style.display = 'none';
      if (tableEl) {
        tableEl.style.display = 'block';
        var columns = (config && Array.isArray(config.columns)) ? config.columns : [];
        var rows = (config && Array.isArray(config.rows)) ? config.rows : [];
        if (rows.length === 0 || columns.length === 0) {
          tableEl.innerHTML = '<p class="text-xs text-slate-500">No rows to preview.</p>';
        } else {
          var html = '<table class="min-w-full text-xs"><thead><tr>';
          columns.forEach(function (col) {
            html += '<th class="border-b border-slate-200 px-2 py-1 text-left font-semibold text-slate-600">' + escapeHtml(col) + '</th>';
          });
          html += '</tr></thead><tbody>';
          rows.slice(0, 12).forEach(function (row) {
            html += '<tr>';
            row.forEach(function (cell) {
              html += '<td class="border-b border-slate-100 px-2 py-1.5 text-slate-700">' + escapeHtml(cell) + '</td>';
            });
            html += '</tr>';
          });
          html += '</tbody></table>';
          tableEl.innerHTML = html;
        }
      }
      return;
    }

    canvas.style.display = '';
    if (kpiEl) kpiEl.style.display = 'none';
    if (tableEl) tableEl.style.display = 'none';

    if (!config || typeof Chart === 'undefined') return;
    try {
      var chartCfg = JSON.parse(JSON.stringify(config));
      if (!chartCfg.options) chartCfg.options = {};
      chartCfg.options.responsive = true;
      chartCfg.options.maintainAspectRatio = false;
      _injectTooltipCallback(chartCfg);
      cbPreviewChart = new Chart(canvas, chartCfg);
    } catch (e) {
      console.warn('DashAI: preview render error', e);
    }
  }

  function submitChartBuilder() {
    if (!validateCbForm()) return;

    var cfg = getApiConfig();
    if (!cfg) return;

    var submitBtn = document.getElementById('cb-submit-btn');
    submitBtn.textContent = 'Adding…';
    submitBtn.disabled = true;

    var submitUrl = cbEditingWidgetId
      ? (cfg.updateWidgetBaseUrl + cbEditingWidgetId + '/update/')
      : cfg.addWidgetUrl;
    fetch(submitUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify(buildPayload(false)),
    })
      .then(function (r) {
        if (!r.ok) throw new Error('Server error ' + r.status);
        return r.json();
      })
      .then(function (data) {
        submitBtn.textContent = cbEditingWidgetId ? 'Save Widget' : 'Add to Dashboard';
        submitBtn.disabled = false;
        if (data.success) {
          closeChartBuilder();
          showToast(cbEditingWidgetId ? 'Widget updated — reloading…' : 'Chart added — reloading…', 'success');
          setTimeout(function () { window.location.reload(); }, 700);
        } else {
          showCbValidationError(data.error || 'Failed to add widget. Please try again.');
        }
      })
      .catch(function (err) {
        submitBtn.textContent = cbEditingWidgetId ? 'Save Widget' : 'Add to Dashboard';
        submitBtn.disabled = false;
        showCbValidationError('Network error: ' + err.message);
      });
  }

  // ── Dynamic Drag Resize (height + width) ──────────────────────────────────

  function initWidgetDragResize() {
    var cfg = getApiConfig();
    if (!cfg) return;

    document.querySelectorAll('.widget-resize-handle').forEach(function (handle) {
      var widgetId = handle.dataset.widgetId;

      function startResize(startX, startY) {
        var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');
        if (!card) return;
        var wrap = card.querySelector('.widget-chart-wrap');
        var startHeight = wrap ? wrap.offsetHeight : (card.offsetHeight || 260);
        var startSize = card.dataset.widgetSize || 'md';
        var pendingWidth = null;
        card.style.userSelect = 'none';

        // Live resize indicator
        var indicator = document.createElement('div');
        indicator.style.cssText = 'position:absolute;bottom:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#6366f1,#a78bfa);border-radius:0 0 1rem 1rem;opacity:0.8;pointer-events:none;z-index:5;';
        card.appendChild(indicator);

        function doResize(currentX, currentY) {
          // Height resize (Y axis)
          var nextH = Math.max(140, Math.min(1200, startHeight + (currentY - startY)));
          if (wrap) wrap.style.height = nextH + 'px';
          card.style.minHeight = (nextH + 60) + 'px';

          // Width resize (X axis) – snap between md and lg
          var dx = currentX - startX;
          if (dx > 80 && startSize !== 'lg' && pendingWidth !== 'lg') {
            pendingWidth = 'lg';
            card.classList.remove('sm:col-span-1', 'sm:col-span-2');
            card.classList.add('sm:col-span-2');
            var wl = card.querySelector('.width-label');
            if (wl) wl.textContent = 'LG';
          } else if (dx < -80 && startSize === 'lg' && pendingWidth !== 'md') {
            pendingWidth = 'md';
            card.classList.remove('sm:col-span-1', 'sm:col-span-2');
            card.classList.add('sm:col-span-1');
            var wl2 = card.querySelector('.width-label');
            if (wl2) wl2.textContent = 'MD';
          }

          var entry = widgetCharts[widgetId];
          if (entry && entry.chart) {
            try { entry.chart.resize(); } catch (_) {}
          }
        }

        function endResize(finalX, finalY) {
          document.removeEventListener('mousemove', onMouseMove);
          document.removeEventListener('mouseup', onMouseUp);
          document.removeEventListener('touchmove', onTouchMove);
          document.removeEventListener('touchend', onTouchEnd);
          card.style.userSelect = '';
          if (indicator.parentElement) indicator.remove();
          var finalHeight = Math.max(140, Math.min(1200, startHeight + (finalY - startY)));
          var finalSize = pendingWidth || startSize;
          if (pendingWidth) card.dataset.widgetSize = pendingWidth;
          fetch(cfg.resizeWidgetBaseUrl + widgetId + '/resize/', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ size: finalSize, height: finalHeight }),
          })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (!data.success) showToast(data.error || 'Resize failed', 'error');
            })
            .catch(function () {});
        }

        function onMouseMove(e) { doResize(e.clientX, e.clientY); }
        function onMouseUp(e) { endResize(e.clientX, e.clientY); }
        function onTouchMove(e) { if (e.touches[0]) doResize(e.touches[0].clientX, e.touches[0].clientY); }
        function onTouchEnd(e) {
          var t = e.changedTouches[0];
          endResize(t ? t.clientX : startX, t ? t.clientY : startY);
        }

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        document.addEventListener('touchmove', onTouchMove, { passive: true });
        document.addEventListener('touchend', onTouchEnd);
      }

      handle.addEventListener('mousedown', function (e) {
        e.preventDefault();
        startResize(e.clientX, e.clientY);
      });

      handle.addEventListener('touchstart', function (e) {
        if (e.touches[0]) startResize(e.touches[0].clientX, e.touches[0].clientY);
      }, { passive: true });
    });
  }

  // ── Widget Width Toggle (sm / md / lg) ────────────────────────────────────

  function initWidgetWidthToggle() {
    var cfg = getApiConfig();
    if (!cfg) return;

    document.querySelectorAll('.widget-width-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var widgetId = btn.dataset.widgetId;
        var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');
        if (!card) return;

        var current = card.dataset.widgetSize || 'md';
        var next = current === 'sm' ? 'md' : (current === 'md' ? 'lg' : 'sm');

        // Update CSS classes
        card.classList.remove('sm:col-span-1', 'sm:col-span-2', 'col-span-full');
        if (next === 'lg') card.classList.add('sm:col-span-2');
        else card.classList.add('sm:col-span-1');

        card.dataset.widgetSize = next;
        btn.title = 'Width: ' + next.toUpperCase();
        btn.querySelector('.width-label') && (btn.querySelector('.width-label').textContent = next.toUpperCase());

        // Save to backend
        fetch(cfg.widgetSpanBaseUrl + widgetId + '/span/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: JSON.stringify({ size: next }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.success) {
              var entry = widgetCharts[widgetId];
              if (entry && entry.chart) setTimeout(function () { try { entry.chart.resize(); } catch (_) {} }, 100);
            }
          })
          .catch(function () {});
      });
    });
  }

  // ── Drag-and-Drop Widget Reorder ─────────────────────────────────────────

  function initWidgetDragOrder() {
    var cfg = getApiConfig();
    if (!cfg || !cfg.reorderWidgetsUrl) return;

    var grid = document.getElementById('widgets-grid');
    if (!grid) return;

    var dragSrc = null;

    function getCards() { return Array.from(grid.querySelectorAll('.widget-card')); }

    function saveOrder() {
      var ids = getCards().map(function (c) { return parseInt(c.dataset.widgetId, 10); });
      fetch(cfg.reorderWidgetsUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
        body: JSON.stringify({ order: ids }),
      }).catch(function () {});
    }

    grid.addEventListener('dragstart', function (e) {
      var card = e.target.closest('.widget-card');
      if (!card) return;
      dragSrc = card;
      card.style.opacity = '0.4';
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.dataset.widgetId);
    });

    grid.addEventListener('dragend', function (e) {
      var card = e.target.closest('.widget-card');
      if (card) card.style.opacity = '';
      grid.querySelectorAll('.widget-card').forEach(function (c) {
        c.classList.remove('drag-over');
      });
      dragSrc = null;
      saveOrder();
    });

    grid.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      var card = e.target.closest('.widget-card');
      if (!card || card === dragSrc) return;
      grid.querySelectorAll('.widget-card').forEach(function (c) { c.classList.remove('drag-over'); });
      card.classList.add('drag-over');
    });

    grid.addEventListener('drop', function (e) {
      e.preventDefault();
      var card = e.target.closest('.widget-card');
      if (!card || !dragSrc || card === dragSrc) return;
      var cards = getCards();
      var srcIdx = cards.indexOf(dragSrc);
      var dstIdx = cards.indexOf(card);
      if (srcIdx < 0 || dstIdx < 0) return;
      if (srcIdx < dstIdx) {
        card.after(dragSrc);
      } else {
        card.before(dragSrc);
      }
      grid.querySelectorAll('.widget-card').forEach(function (c) { c.classList.remove('drag-over'); });
    });

    // Make cards draggable via drag handle
    getCards().forEach(function (card) {
      var handle = card.querySelector('.widget-drag-handle');
      if (handle) {
        handle.addEventListener('mouseenter', function () { card.setAttribute('draggable', 'true'); });
        handle.addEventListener('mouseleave', function () { if (!dragSrc) card.setAttribute('draggable', 'false'); });
      }
    });
  }

  // ── Inline Insert Zones (type anywhere / add sections) ────────────────────
  // Card-level hover buttons for inserting sections above/below each widget.
  // Uses absolute positioning so the grid layout is NOT affected.

  function initInsertZones() {
    var cfg = getApiConfig();
    if (!cfg) return;

    var grid = document.getElementById('widgets-grid');
    if (!grid) return;

    var activePanel = null;

    function closePanel() {
      if (activePanel && activePanel.parentElement) activePanel.remove();
      activePanel = null;
    }

    function openQuickPanel(anchorCard, afterWidgetId) {
      closePanel();
      var panel = document.createElement('div');
      panel.className = 'insert-floating-panel';
      panel.style.cssText = 'position:fixed;z-index:80;background:#fff;border:1px solid #c7d2fe;border-radius:0.875rem;box-shadow:0 10px 30px rgba(99,102,241,0.15);padding:0.75rem;min-width:22rem;';

      panel.innerHTML =
        '<div class="flex items-center gap-2">' +
        '<input id="qa-input" type="text" placeholder="Type section name… (Enter to add heading)" autocomplete="off"' +
        ' style="flex:1;border:1px solid #e2e8f0;border-radius:0.5rem;padding:0.375rem 0.75rem;font-size:0.875rem;outline:none;">' +
        '<button id="qa-section-btn" style="background:#4f46e5;color:#fff;border:none;border-radius:0.5rem;padding:0.375rem 0.75rem;font-size:0.75rem;font-weight:600;cursor:pointer;white-space:nowrap;">Section</button>' +
        '<button id="qa-divider-btn" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:0.5rem;padding:0.375rem 0.75rem;font-size:0.75rem;font-weight:600;cursor:pointer;white-space:nowrap;color:#475569;">Divider</button>' +
        '<button id="qa-cancel-btn" style="background:none;border:none;cursor:pointer;color:#94a3b8;font-size:1rem;padding:0 0.25rem;">✕</button>' +
        '</div>';

      // Position below the anchor card
      document.body.appendChild(panel);
      var rect = anchorCard.getBoundingClientRect();
      var panelW = 352;
      var left = Math.min(rect.left, window.innerWidth - panelW - 16);
      panel.style.top = (rect.bottom + window.scrollY + 6) + 'px';
      panel.style.left = Math.max(8, left) + 'px';
      activePanel = panel;

      var input = panel.querySelector('#qa-input');
      setTimeout(function () { input.focus(); }, 10);

      function submit(type) {
        var text = input.value.trim();
        if (type === 'section' && !text) {
          input.style.borderColor = '#f43f5e';
          setTimeout(function () { input.style.borderColor = '#e2e8f0'; }, 800);
          return;
        }
        var url = type === 'section' ? cfg.addHeadingUrl : cfg.addDividerUrl;
        var payload = type === 'section'
          ? { text: text, font_size: '2xl', color: 'slate', font_family: 'inter', align: 'left', after_widget_id: afterWidgetId }
          : { label: text, after_widget_id: afterWidgetId };
        fetch(url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: JSON.stringify(payload),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.success) {
              showToast(type === 'section' ? 'Section added' : 'Divider added', 'success');
              closePanel();
              setTimeout(function () { window.location.reload(); }, 300);
            } else {
              showToast(data.error || 'Failed to add', 'error');
            }
          })
          .catch(function () { showToast('Network error', 'error'); });
      }

      panel.querySelector('#qa-section-btn').addEventListener('click', function () { submit('section'); });
      panel.querySelector('#qa-divider-btn').addEventListener('click', function () { submit('divider'); });
      panel.querySelector('#qa-cancel-btn').addEventListener('click', closePanel);
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); submit('section'); }
        if (e.key === 'Escape') closePanel();
      });

      setTimeout(function () {
        document.addEventListener('click', function onDocClick(e) {
          if (!panel.contains(e.target)) {
            closePanel();
            document.removeEventListener('click', onDocClick);
          }
        });
      }, 50);
    }

    // Add a small absolute-positioned "Insert after" button on each widget card
    var cards = Array.from(grid.querySelectorAll('.widget-card'));
    cards.forEach(function (card) {
      var widgetId = card.dataset.widgetId;
      var insertBtn = document.createElement('button');
      insertBtn.className = 'card-insert-btn';
      insertBtn.title = 'Add section or divider after this widget';
      insertBtn.innerHTML = '<span style="font-size:1rem;line-height:1;">+</span>';
      insertBtn.style.cssText = [
        'position:absolute', 'bottom:-12px', 'left:50%', 'transform:translateX(-50%)',
        'z-index:20', 'background:#fff', 'border:1px solid #c7d2fe', 'border-radius:50%',
        'width:22px', 'height:22px', 'display:flex', 'align-items:center', 'justify-content:center',
        'cursor:pointer', 'opacity:0', 'transition:opacity 0.15s', 'color:#6366f1',
        'font-weight:700', 'box-shadow:0 2px 6px rgba(99,102,241,0.18)',
      ].join(';');

      card.style.overflow = 'visible';
      card.appendChild(insertBtn);

      card.addEventListener('mouseenter', function () { insertBtn.style.opacity = '1'; });
      card.addEventListener('mouseleave', function (e) {
        if (!insertBtn.contains(e.relatedTarget)) insertBtn.style.opacity = '0';
      });
      insertBtn.addEventListener('mouseenter', function () { insertBtn.style.opacity = '1'; });
      insertBtn.addEventListener('mouseleave', function () { insertBtn.style.opacity = '0'; });

      insertBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        openQuickPanel(card, widgetId || '0');
      });
    });
  }

  // ── Presentation Mode ────────────────────────────────────────────────────

  var presentationChart = null;
  var presentationSlides = [];
  var presentationIndex = 0;

  function initPresentationMode() {
    var openBtn = document.getElementById('open-presentation-btn');
    var overlay = document.getElementById('presentation-overlay');
    var closeBtn = document.getElementById('presentation-close-btn');
    var prevBtn = document.getElementById('presentation-prev-btn');
    var nextBtn = document.getElementById('presentation-next-btn');
    var counterEl = document.getElementById('presentation-counter');
    var titleEl = document.getElementById('presentation-widget-title');
    var canvas = document.getElementById('presentation-canvas');
    var tableWrap = document.getElementById('presentation-table-wrap');
    var kpiWrap = document.getElementById('presentation-kpi-wrap');
    var textWrap = document.getElementById('presentation-text-wrap');
    var addTextBtn = document.getElementById('presentation-add-text-btn');
    var chartSelect = document.getElementById('presentation-chart-select');
    var addChartBtn = document.getElementById('presentation-add-chart-btn');
    var aiSlideBtn = document.getElementById('presentation-ai-slide-btn');
    var themeSelect = document.getElementById('presentation-theme-select');
    var bgColorInput = document.getElementById('presentation-bg-color');

    if (!openBtn || !overlay) return;

    function buildWidgetList() {
      // Exclude divider widgets from presentation (they're visual separators only)
      var widgetIds = Array.from(document.querySelectorAll('.widget-card')).filter(function (card) {
        return card.dataset.widgetType !== 'divider';
      }).map(function (card) {
        return card.dataset.widgetId;
      }).filter(Boolean);
      presentationSlides = widgetIds.map(function (id) { return { kind: 'widget', widgetId: id }; });
      if (chartSelect) {
        chartSelect.innerHTML = '<option value="">Select chart widget</option>';
        widgetIds.forEach(function (id) {
          var card = document.querySelector('.widget-card[data-widget-id="' + id + '"]');
          if (!card) return;
          if (card.dataset.widgetType === 'heading' || card.dataset.widgetType === 'text_canvas') return;
          var titleNode = card.querySelector('.widget-title');
          var opt = document.createElement('option');
          opt.value = id;
          opt.textContent = (titleNode ? titleNode.textContent.trim() : ('Widget ' + id)).slice(0, 60);
          chartSelect.appendChild(opt);
        });
      }
    }

    function clearPresentationAreas() {
      if (presentationChart) {
        try { presentationChart.destroy(); } catch (_) {}
        presentationChart = null;
      }
      if (canvas) {
        canvas.style.display = 'none';
        // Clear canvas context to avoid Chart.js reuse issues
        try {
          var ctx = canvas.getContext('2d');
          if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
        } catch (_) {}
      }
      if (tableWrap) { tableWrap.style.display = 'none'; tableWrap.innerHTML = ''; }
      if (kpiWrap) { kpiWrap.style.display = 'none'; kpiWrap.innerHTML = ''; }
      if (textWrap) { textWrap.style.display = 'none'; textWrap.innerHTML = ''; }
    }

    function renderSlide(index) {
      if (!presentationSlides.length) return;
      presentationIndex = Math.max(0, Math.min(index, presentationSlides.length - 1));
      var slide = presentationSlides[presentationIndex];
      if (!slide) return;
      var widgetId = slide.widgetId;
      var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');

      if (counterEl) counterEl.textContent = (presentationIndex + 1) + ' / ' + presentationSlides.length;
      if (prevBtn) prevBtn.disabled = presentationIndex === 0;
      if (nextBtn) nextBtn.disabled = presentationIndex === presentationSlides.length - 1;

      if (slide.kind === 'text') {
        clearPresentationAreas();
        if (titleEl) titleEl.textContent = slide.title || 'Text Slide';
        if (textWrap) {
          textWrap.style.display = 'block';
          textWrap.style.background = 'rgba(255,255,255,0.08)';
          textWrap.innerHTML = '<div style="max-width:920px;margin:0 auto;"><h3 style="font-size:1.6rem;font-weight:700;margin-bottom:0.75rem;">' + escapeHtml(slide.title || 'Notes') + '</h3><p style="white-space:pre-wrap;font-size:1.05rem;line-height:1.7;">' + escapeHtml(slide.content || '') + '</p></div>';
        }
        return;
      }

      // Get title
      var titleText = '';
      if (card) {
        var titleNode = card.querySelector('.widget-title');
        if (titleNode) titleText = titleNode.textContent.trim();
      }
      if (titleEl) titleEl.textContent = titleText;

      clearPresentationAreas();

      // Chart widget
      var entry = widgetCharts[widgetId];
      if (entry && entry.config && canvas) {
        canvas.style.display = 'block';
        // Deep clone config to avoid mutation
        var slideCfg;
        try { slideCfg = JSON.parse(JSON.stringify(entry.config)); } catch (_) { slideCfg = entry.config; }
        if (!slideCfg.options) slideCfg.options = {};
        slideCfg.options.responsive = true;
        slideCfg.options.maintainAspectRatio = false;
        slideCfg.options.animation = { duration: 400, easing: 'easeInOutQuart' };
        // Use requestAnimationFrame to ensure canvas is ready
        requestAnimationFrame(function () {
          try { presentationChart = new Chart(canvas, slideCfg); } catch (e) {
            console.warn('DashAI: presentation chart error', e);
            canvas.style.display = 'none';
            if (textWrap) {
              textWrap.style.display = 'flex';
              textWrap.innerHTML = '<p style="color:#94a3b8;font-size:0.9rem;">Unable to render chart</p>';
            }
          }
        });
        return;
      }

      if (!card) return;

      // KPI widget
      var kpiEl = card.querySelector('.widget-kpi-value');
      if (kpiEl && kpiWrap) {
        kpiWrap.style.display = 'flex';
        kpiWrap.innerHTML = kpiEl.parentElement.innerHTML;
        return;
      }

      // Table widget
      var tbl = card.querySelector('table');
      if (tbl && tableWrap) {
        tableWrap.style.display = 'block';
        tableWrap.innerHTML = tbl.parentElement.innerHTML;
        return;
      }

      // Text canvas widget
      var textContent = card.querySelector('.widget-text-canvas-content');
      if (textContent && textWrap) {
        textWrap.style.display = 'block';
        textWrap.innerHTML = textContent.innerHTML;
        return;
      }

      // Heading widget – show text prominently
      var headingEl = card.querySelector('.widget-heading-display');
      if (headingEl && textWrap) {
        textWrap.style.display = 'flex';
        textWrap.style.alignItems = 'center';
        textWrap.style.justifyContent = 'center';
        textWrap.innerHTML = '<div style="font-size:2.5rem;font-weight:800;color:#e2e8f0;text-align:center;">' + headingEl.textContent + '</div>';
        return;
      }
    }

    openBtn.addEventListener('click', function () {
      buildWidgetList();
      if (!presentationSlides.length) { showToast('No widgets to present', 'info'); return; }
      overlay.style.display = 'flex';
      document.body.style.overflow = 'hidden';
      renderSlide(0);
    });

    function closePresentation() {
      overlay.style.display = 'none';
      document.body.style.overflow = '';
      if (presentationChart) { try { presentationChart.destroy(); } catch (_) {} presentationChart = null; }
    }

    if (closeBtn) closeBtn.addEventListener('click', closePresentation);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) closePresentation(); });
    if (prevBtn) prevBtn.addEventListener('click', function () { renderSlide(presentationIndex - 1); });
    if (nextBtn) nextBtn.addEventListener('click', function () { renderSlide(presentationIndex + 1); });

    if (addTextBtn) {
      addTextBtn.addEventListener('click', function () {
        var title = window.prompt('Slide title', 'Executive Summary');
        if (title === null) return;
        var content = window.prompt('Slide content', 'Add your talking points here.');
        if (content === null) return;
        presentationSlides.push({ kind: 'text', title: title || 'Notes', content: content || '' });
        renderSlide(presentationSlides.length - 1);
      });
    }
    if (addChartBtn && chartSelect) {
      addChartBtn.addEventListener('click', function () {
        var selectedId = chartSelect.value;
        if (!selectedId) { showToast('Select a chart widget first', 'info'); return; }
        presentationSlides.push({ kind: 'widget', widgetId: selectedId });
        renderSlide(presentationSlides.length - 1);
      });
    }
    if (aiSlideBtn) {
      aiSlideBtn.addEventListener('click', function () {
        var cfg = getApiConfig();
        if (!cfg || !cfg.executiveSummaryUrl) { showToast('AI summary endpoint is unavailable.', 'error'); return; }
        aiSlideBtn.disabled = true;
        aiSlideBtn.textContent = 'Generating…';
        fetch(cfg.executiveSummaryUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: '{}',
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            aiSlideBtn.disabled = false;
            aiSlideBtn.textContent = 'Generate AI Slide';
            if (!data.success || !data.summary) { showToast('Could not generate AI slide.', 'error'); return; }
            presentationSlides.push({ kind: 'text', title: 'AI Executive Summary', content: data.summary });
            renderSlide(presentationSlides.length - 1);
          })
          .catch(function () {
            aiSlideBtn.disabled = false;
            aiSlideBtn.textContent = 'Generate AI Slide';
            showToast('AI slide generation failed.', 'error');
          });
      });
    }

    function applyPresentationTheme(theme) {
      if (!overlay) return;
      if (theme === 'light') overlay.style.background = 'rgba(241,245,249,0.98)';
      else if (theme === 'indigo') overlay.style.background = 'linear-gradient(135deg,#1e1b4b,#3730a3,#4338ca)';
      else if (theme === 'emerald') overlay.style.background = 'linear-gradient(135deg,#052e2b,#065f46,#047857)';
      else overlay.style.background = 'rgba(2,6,23,0.97)';
    }
    if (themeSelect) themeSelect.addEventListener('change', function () { applyPresentationTheme(themeSelect.value); });
    if (bgColorInput) {
      bgColorInput.addEventListener('input', function () {
        if (!overlay) return;
        overlay.style.background = bgColorInput.value;
      });
    }

    document.addEventListener('keydown', function (e) {
      if (overlay.style.display === 'none' || overlay.style.display === '') return;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') renderSlide(presentationIndex - 1);
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') renderSlide(presentationIndex + 1);
      if (e.key === 'Escape') closePresentation();
    });
  }

  // ── Text Canvas Builder ──────────────────────────────────────────────────

  function initTextCanvasBuilder() {
    var cfg = getApiConfig();
    if (!cfg || !cfg.addTextCanvasUrl) return;

    var openBtn = document.getElementById('open-text-canvas-btn');
    var modal = document.getElementById('text-canvas-modal');
    var overlay = document.getElementById('text-canvas-overlay');
    var closeBtn = document.getElementById('close-text-canvas-btn');
    var cancelBtn = document.getElementById('cancel-text-canvas-btn');
    var submitBtn = document.getElementById('submit-text-canvas-btn');
    var contentInput = document.getElementById('tc-content');
    var titleInput = document.getElementById('tc-title');
    var errorEl = document.getElementById('tc-error');

    if (!openBtn || !modal) return;

    function openModal() {
      if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }
      if (contentInput) contentInput.value = '';
      if (titleInput) titleInput.value = '';
      modal.style.display = 'block';
      if (overlay) overlay.style.display = 'block';
      setTimeout(function () { if (contentInput) contentInput.focus(); }, 20);
    }

    function closeModal() {
      modal.style.display = 'none';
      if (overlay) overlay.style.display = 'none';
    }

    openBtn.addEventListener('click', openModal);
    if (overlay) overlay.addEventListener('click', closeModal);
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

    if (submitBtn) {
      submitBtn.addEventListener('click', function () {
        var content = contentInput ? contentInput.value.trim() : '';
        if (!content) {
          if (errorEl) { errorEl.textContent = 'Content is required.'; errorEl.style.display = 'block'; }
          return;
        }
        submitBtn.disabled = true;
        submitBtn.textContent = 'Adding…';

        fetch(cfg.addTextCanvasUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: JSON.stringify({
            title: (titleInput ? titleInput.value.trim() : '') || 'Text Block',
            content: content,
            bg_color: (document.getElementById('tc-bg-color') || {}).value || 'white',
            text_size: (document.getElementById('tc-text-size') || {}).value || 'sm',
          }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Add Text Block';
            if (data.success) {
              closeModal();
              showToast('Text block added — reloading…', 'success');
              setTimeout(function () { window.location.reload(); }, 600);
            } else {
              if (errorEl) { errorEl.textContent = data.error || 'Could not add text block.'; errorEl.style.display = 'block'; }
            }
          })
          .catch(function (err) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Add Text Block';
            if (errorEl) { errorEl.textContent = 'Network error: ' + err.message; errorEl.style.display = 'block'; }
          });
      });
    }
  }

  // ── Table Sort & Search ──────────────────────────────────────────────────

  function initTableInteractions() {
    document.querySelectorAll('.widget-table-wrap').forEach(function (wrap) {
      var table = wrap.querySelector('table');
      if (!table) return;

      // Search filter
      var searchInput = wrap.querySelector('.table-search-input');
      if (searchInput) {
        searchInput.addEventListener('input', function () {
          var query = searchInput.value.toLowerCase();
          table.querySelectorAll('tbody tr').forEach(function (row) {
            var text = row.textContent.toLowerCase();
            row.style.display = text.includes(query) ? '' : 'none';
          });
        });
      }

      // Column sort
      table.querySelectorAll('th[data-sort-col]').forEach(function (th) {
        th.style.cursor = 'pointer';
        th.addEventListener('click', function () {
          var colIdx = parseInt(th.dataset.sortCol, 10);
          var asc = th.dataset.sortDir !== 'asc';
          th.dataset.sortDir = asc ? 'asc' : 'desc';

          // Reset other headers
          table.querySelectorAll('th[data-sort-col]').forEach(function (h) {
            if (h !== th) { h.dataset.sortDir = ''; h.querySelector('.sort-icon') && (h.querySelector('.sort-icon').textContent = '↕'); }
          });
          th.querySelector('.sort-icon') && (th.querySelector('.sort-icon').textContent = asc ? '↑' : '↓');

          var tbody = table.querySelector('tbody');
          if (!tbody) return;
          var rows = Array.from(tbody.querySelectorAll('tr'));
          rows.sort(function (a, b) {
            var va = (a.cells[colIdx] || {}).textContent || '';
            var vb = (b.cells[colIdx] || {}).textContent || '';
            var na = parseFloat(va.replace(/,/g, ''));
            var nb = parseFloat(vb.replace(/,/g, ''));
            if (!isNaN(na) && !isNaN(nb)) return asc ? na - nb : nb - na;
            return asc ? va.localeCompare(vb) : vb.localeCompare(va);
          });
          rows.forEach(function (row) { tbody.appendChild(row); });
        });
      });
    });
  }

  function initWidgetEdit() {
    document.querySelectorAll('.edit-widget-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var parsedConfig = null;
        if (btn.dataset.widgetConfig) {
          try { parsedConfig = JSON.parse(btn.dataset.widgetConfig); } catch (_) {}
        }
        openChartBuilder({
          widgetId: btn.dataset.widgetId,
          type: btn.dataset.widgetType || 'bar',
          title: btn.dataset.widgetTitle || '',
          config: parsedConfig,
        });
      });
    });
  }

  function initDashboardRename() {
    var cfg = getApiConfig();
    if (!cfg || !cfg.renameDashboardUrl) return;
    var titleEl = document.getElementById('dashboard-title');
    var inputEl = document.getElementById('dashboard-title-input');
    if (!titleEl || !inputEl) return;
    titleEl.addEventListener('click', function () {
      titleEl.classList.add('hidden');
      inputEl.classList.remove('hidden');
      inputEl.focus();
      inputEl.select();
    });
    function commit() {
      var v = inputEl.value.trim();
      if (!v) return cancel();
      fetch(cfg.renameDashboardUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
        body: JSON.stringify({ title: v }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.success) throw new Error(data.error || 'Rename failed');
          titleEl.textContent = data.title;
          showToast('Dashboard renamed', 'success');
          cancel();
        })
        .catch(function (err) { showToast(err.message, 'error'); cancel(); });
    }
    function cancel() {
      inputEl.classList.add('hidden');
      titleEl.classList.remove('hidden');
    }
    inputEl.addEventListener('blur', commit);
    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { cancel(); }
    });
  }

  function initHeadingBuilder() {
    var cfg = getApiConfig();
    if (!cfg || !cfg.addHeadingUrl) return;
    var openBtn = document.getElementById('open-heading-builder-btn');
    var modal = document.getElementById('heading-builder-modal');
    var overlay = document.getElementById('heading-builder-overlay');
    var closeBtn = document.getElementById('close-heading-builder-btn');
    var cancelBtn = document.getElementById('cancel-heading-builder-btn');
    var submitBtn = document.getElementById('submit-heading-builder-btn');
    var textInput = document.getElementById('hb-text');
    var errorEl = document.getElementById('hb-error');
    if (!openBtn || !modal || !overlay || !submitBtn || !textInput) return;

    function openModal() {
      if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }
      textInput.value = '';
      modal.style.display = 'block';
      overlay.style.display = 'block';
      setTimeout(function () { textInput.focus(); }, 20);
    }
    function closeModal() {
      modal.style.display = 'none';
      overlay.style.display = 'none';
    }

    openBtn.addEventListener('click', openModal);
    overlay.addEventListener('click', closeModal);
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

    submitBtn.addEventListener('click', function () {
      var payload = {
        text: (textInput.value || '').trim(),
        font_size: (document.getElementById('hb-font-size') || {}).value || '2xl',
        color: (document.getElementById('hb-color') || {}).value || 'indigo',
        font_family: (document.getElementById('hb-font-family') || {}).value || 'inter',
        align: (document.getElementById('hb-align') || {}).value || 'left',
        after_widget_id: (document.getElementById('hb-after-widget') || {}).value || '0',
      };
      if (!payload.text) {
        if (errorEl) {
          errorEl.textContent = 'Please enter heading text.';
          errorEl.style.display = 'block';
        }
        return;
      }
      submitBtn.disabled = true;
      submitBtn.textContent = 'Adding…';
      fetch(cfg.addHeadingUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
        body: JSON.stringify(payload),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.success) throw new Error(data.error || 'Failed to add heading');
          showToast('Heading added', 'success');
          closeModal();
          setTimeout(function () { window.location.reload(); }, 300);
        })
        .catch(function (err) {
          if (errorEl) {
            errorEl.textContent = err.message;
            errorEl.style.display = 'block';
          }
        })
        .finally(function () {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Add Heading';
        });
    });
  }

  // ── Section (Heading / Text Canvas) Inline Edit ──────────────────────────

  function initSectionEdit() {
    var cfg = getApiConfig();
    if (!cfg) return;

    // — Heading edit modal elements —
    var ehOverlay = document.getElementById('edit-heading-overlay');
    var ehModal = document.getElementById('edit-heading-modal');
    var ehText = document.getElementById('eh-text');
    var ehFontSize = document.getElementById('eh-font-size');
    var ehColor = document.getElementById('eh-color');
    var ehFontFamily = document.getElementById('eh-font-family');
    var ehAlign = document.getElementById('eh-align');
    var ehError = document.getElementById('eh-error');
    var ehSubmit = document.getElementById('submit-edit-heading-btn');
    var ehClose = document.getElementById('close-edit-heading-btn');
    var ehCancel = document.getElementById('cancel-edit-heading-btn');

    // — Text canvas edit modal elements —
    var etcOverlay = document.getElementById('edit-text-canvas-overlay');
    var etcModal = document.getElementById('edit-text-canvas-modal');
    var etcTitle = document.getElementById('etc-title');
    var etcContent = document.getElementById('etc-content');
    var etcBgColor = document.getElementById('etc-bg-color');
    var etcTextSize = document.getElementById('etc-text-size');
    var etcError = document.getElementById('etc-error');
    var etcSubmit = document.getElementById('submit-edit-text-canvas-btn');
    var etcClose = document.getElementById('close-edit-text-canvas-btn');
    var etcCancel = document.getElementById('cancel-edit-text-canvas-btn');

    var activeWidgetId = null;

    function setSelectValue(el, val) {
      if (!el || !val) return;
      for (var i = 0; i < el.options.length; i++) {
        if (el.options[i].value === val) { el.selectedIndex = i; break; }
      }
    }

    function openHeadingEdit(widgetId, widgetConfig, widgetTitle) {
      activeWidgetId = widgetId;
      var config = {};
      try { config = JSON.parse(widgetConfig); } catch (_) {}
      if (ehText) ehText.value = config.text || widgetTitle || '';
      setSelectValue(ehFontSize, config.font_size || '2xl');
      setSelectValue(ehColor, config.color || 'indigo');
      setSelectValue(ehFontFamily, config.font_family || 'inter');
      setSelectValue(ehAlign, config.align || 'left');
      if (ehError) { ehError.style.display = 'none'; ehError.textContent = ''; }
      if (ehModal) ehModal.style.display = 'block';
      if (ehOverlay) ehOverlay.style.display = 'block';
      setTimeout(function () { if (ehText) ehText.focus(); }, 20);
    }

    function closeHeadingEdit() {
      if (ehModal) ehModal.style.display = 'none';
      if (ehOverlay) ehOverlay.style.display = 'none';
      activeWidgetId = null;
    }

    function openTextCanvasEdit(widgetId, widgetConfig, widgetTitle) {
      activeWidgetId = widgetId;
      var config = {};
      try { config = JSON.parse(widgetConfig); } catch (_) {}
      if (etcTitle) etcTitle.value = widgetTitle || '';
      if (etcContent) etcContent.value = config.content || '';
      setSelectValue(etcBgColor, config.bg_color || 'white');
      setSelectValue(etcTextSize, config.text_size || 'sm');
      if (etcError) { etcError.style.display = 'none'; etcError.textContent = ''; }
      if (etcModal) etcModal.style.display = 'block';
      if (etcOverlay) etcOverlay.style.display = 'block';
      setTimeout(function () { if (etcContent) etcContent.focus(); }, 20);
    }

    function closeTextCanvasEdit() {
      if (etcModal) etcModal.style.display = 'none';
      if (etcOverlay) etcOverlay.style.display = 'none';
      activeWidgetId = null;
    }

    // Wire up edit section buttons
    document.querySelectorAll('.edit-section-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var widgetId = btn.dataset.widgetId;
        var widgetType = btn.dataset.widgetType;
        var widgetConfig = btn.dataset.widgetConfig || '{}';
        var widgetTitle = btn.dataset.widgetTitle || '';
        if (widgetType === 'heading') {
          openHeadingEdit(widgetId, widgetConfig, widgetTitle);
        } else if (widgetType === 'text_canvas') {
          openTextCanvasEdit(widgetId, widgetConfig, widgetTitle);
        }
      });
    });

    // Heading modal close/cancel
    if (ehClose) ehClose.addEventListener('click', closeHeadingEdit);
    if (ehCancel) ehCancel.addEventListener('click', closeHeadingEdit);
    if (ehOverlay) ehOverlay.addEventListener('click', closeHeadingEdit);

    // Text canvas modal close/cancel
    if (etcClose) etcClose.addEventListener('click', closeTextCanvasEdit);
    if (etcCancel) etcCancel.addEventListener('click', closeTextCanvasEdit);
    if (etcOverlay) etcOverlay.addEventListener('click', closeTextCanvasEdit);

    // Heading submit
    if (ehSubmit) {
      ehSubmit.addEventListener('click', function () {
        var text = ehText ? ehText.value.trim() : '';
        if (!text) {
          if (ehError) { ehError.textContent = 'Heading text is required.'; ehError.style.display = 'block'; }
          return;
        }
        ehSubmit.disabled = true;
        ehSubmit.textContent = 'Saving…';
        var url = (cfg.updateHeadingBaseUrl || '') + activeWidgetId + '/update-heading/';
        fetch(url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: JSON.stringify({
            text: text,
            font_size: (ehFontSize || {}).value || '2xl',
            color: (ehColor || {}).value || 'indigo',
            font_family: (ehFontFamily || {}).value || 'inter',
            align: (ehAlign || {}).value || 'left',
          }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            ehSubmit.disabled = false;
            ehSubmit.textContent = 'Save Heading';
            if (data.success) {
              closeHeadingEdit();
              showToast('Heading updated', 'success');
              setTimeout(function () { window.location.reload(); }, 300);
            } else {
              if (ehError) { ehError.textContent = data.error || 'Could not update heading.'; ehError.style.display = 'block'; }
            }
          })
          .catch(function (err) {
            ehSubmit.disabled = false;
            ehSubmit.textContent = 'Save Heading';
            if (ehError) { ehError.textContent = 'Network error: ' + err.message; ehError.style.display = 'block'; }
          });
      });
    }

    // Text canvas submit
    if (etcSubmit) {
      etcSubmit.addEventListener('click', function () {
        var content = etcContent ? etcContent.value.trim() : '';
        if (!content) {
          if (etcError) { etcError.textContent = 'Content is required.'; etcError.style.display = 'block'; }
          return;
        }
        etcSubmit.disabled = true;
        etcSubmit.textContent = 'Saving…';
        var url = (cfg.updateTextCanvasBaseUrl || '') + activeWidgetId + '/update-text-canvas/';
        fetch(url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: JSON.stringify({
            title: (etcTitle || {}).value || '',
            content: content,
            bg_color: (etcBgColor || {}).value || 'white',
            text_size: (etcTextSize || {}).value || 'sm',
          }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            etcSubmit.disabled = false;
            etcSubmit.textContent = 'Save Text Block';
            if (data.success) {
              closeTextCanvasEdit();
              showToast('Text block updated', 'success');
              setTimeout(function () { window.location.reload(); }, 300);
            } else {
              if (etcError) { etcError.textContent = data.error || 'Could not update text block.'; etcError.style.display = 'block'; }
            }
          })
          .catch(function (err) {
            etcSubmit.disabled = false;
            etcSubmit.textContent = 'Save Text Block';
            if (etcError) { etcError.textContent = 'Network error: ' + err.message; etcError.style.display = 'block'; }
          });
      });
    }
  }

  function initChartBuilder() {
    var openBtn = document.getElementById('open-chart-builder-btn');
    if (!openBtn) return;

    var closeBtn = document.getElementById('close-chart-builder-btn');
    var overlay = document.getElementById('chart-builder-overlay');
    var previewBtn = document.getElementById('cb-preview-btn');
    var submitBtn = document.getElementById('cb-submit-btn');

    openBtn.addEventListener('click', openChartBuilder);
    if (closeBtn) closeBtn.addEventListener('click', closeChartBuilder);
    if (overlay) overlay.addEventListener('click', closeChartBuilder);
    if (previewBtn) previewBtn.addEventListener('click', previewChart);
    if (submitBtn) submitBtn.addEventListener('click', submitChartBuilder);

    document.querySelectorAll('input[name="cb_chart_type"]').forEach(function (radio) {
      radio.addEventListener('change', updateCbFieldVisibility);
    });

    // Palette click: update visual selection immediately
    document.querySelectorAll('input[name="cb_palette"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        document.querySelectorAll('.cb-palette-option').forEach(function (lbl) {
          var inp = lbl.querySelector('input[name="cb_palette"]');
          var span = lbl.querySelector('span');
          if (!inp || !span) return;
          if (inp.checked) {
            span.style.borderColor = '#6d28d9';
            span.style.backgroundColor = '#f5f3ff';
          } else {
            span.style.borderColor = '';
            span.style.backgroundColor = '';
          }
        });
      });
    });

    // When dataset selector changes, reload columns for the new dataset
    var datasetSel = document.getElementById('cb-dataset');
    if (datasetSel) {
      datasetSel.addEventListener('change', function () {
        var loadingEl = document.getElementById('cb-loading');
        var formEl = document.getElementById('cb-form');
        if (loadingEl) loadingEl.style.display = 'flex';
        if (formEl) formEl.style.display = 'none';

        var cfg2 = getApiConfig();
        if (!cfg2) return;
        var vId = datasetSel.value;
        var url2 = cfg2.columnsUrl + (vId ? '?version_id=' + encodeURIComponent(vId) : '');
        fetch(url2, { credentials: 'same-origin' })
          .then(function (r) {
            if (!r.ok) throw new Error('Server returned ' + r.status);
            return r.json();
          })
          .then(function (data) {
            populateCbForm(data);
            if (loadingEl) loadingEl.style.display = 'none';
            if (formEl) formEl.style.display = 'block';
            updateCbFieldVisibility(true);
          })
          .catch(function (err) {
            showCbError('Could not load columns: ' + err.message);
          });
      });
    }

    var dimSel = document.getElementById('cb-dimension');
    var measureSel = document.getElementById('cb-measure');
    var measuresSel = document.getElementById('cb-measures');
    var xSel = document.getElementById('cb-x-measure');
    var ySel = document.getElementById('cb-y-measure');

    function resetTitle() {
      document.getElementById('cb-title').value = '';
      autoSetTitle();
    }

    if (dimSel) dimSel.addEventListener('change', resetTitle);
    if (measureSel) measureSel.addEventListener('change', resetTitle);
    if (measuresSel) measuresSel.addEventListener('change', resetTitle);
    if (xSel) xSel.addEventListener('change', resetTitle);
    if (ySel) ySel.addEventListener('change', resetTitle);

    var aiPrompt = document.getElementById('cb-ai-prompt');
    if (aiPrompt) {
      aiPrompt.addEventListener('input', function () {
        updateAiPromptCounter();
        if (getSelectedChartType() === 'smart') {
          document.getElementById('cb-title').value = '';
          autoSetTitle();
        }
      });
    }

    document.querySelectorAll('.cb-prompt-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var text = chip.dataset.prompt || '';
        var promptEl = document.getElementById('cb-ai-prompt');
        if (!promptEl) return;
        promptEl.value = text;
        updateAiPromptCounter();
        if (getSelectedChartType() === 'smart') {
          document.getElementById('cb-title').value = '';
          autoSetTitle();
        }
        promptEl.focus();
      });
    });

    updateAiPromptCounter();

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        var modal = document.getElementById('chart-builder-modal');
        if (modal && modal.style.display !== 'none') closeChartBuilder();
      }
    });
  }

  // ── Multi-Dataset Panel ──────────────────────────────────────────────────

  function initDatasetsPanel() {
    var cfg = getApiConfig();
    if (!cfg) return;

    // "Use" button – select dataset in chart builder and open it
    document.querySelectorAll('.select-dataset-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var versionId = btn.dataset.versionId;
        var datasetSel = document.getElementById('cb-dataset');
        if (datasetSel && versionId) {
          datasetSel.value = versionId;
        }
        openChartBuilder();
      });
    });

    // "Add dataset" panel toggle
    var openAddBtn = document.getElementById('open-add-dataset-btn');
    var addPanel = document.getElementById('add-dataset-panel');
    var cancelAddBtn = document.getElementById('cancel-add-dataset-btn');
    var confirmAddBtn = document.getElementById('confirm-add-dataset-btn');
    var addSelect = document.getElementById('add-dataset-select');
    var addError = document.getElementById('add-dataset-error');

    if (openAddBtn && addPanel) {
      openAddBtn.addEventListener('click', function () {
        addPanel.style.display = addPanel.style.display === 'none' ? 'block' : 'none';
      });
    }
    if (cancelAddBtn && addPanel) {
      cancelAddBtn.addEventListener('click', function () {
        addPanel.style.display = 'none';
      });
    }

    if (confirmAddBtn && addSelect) {
      confirmAddBtn.addEventListener('click', function () {
        var versionId = addSelect.value;
        if (!versionId) {
          if (addError) { addError.textContent = 'Please select a dataset.'; addError.style.display = 'block'; }
          return;
        }
        if (addError) addError.style.display = 'none';
        confirmAddBtn.disabled = true;
        confirmAddBtn.textContent = 'Linking…';

        fetch(cfg.addDatasetUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: JSON.stringify({ version_id: parseInt(versionId, 10) }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            confirmAddBtn.disabled = false;
            confirmAddBtn.textContent = 'Link';
            if (data.success) {
              showToast('Dataset linked — reloading…', 'success');
              setTimeout(function () { window.location.reload(); }, 600);
            } else {
              if (addError) { addError.textContent = data.error || 'Could not link dataset.'; addError.style.display = 'block'; }
            }
          })
          .catch(function (err) {
            confirmAddBtn.disabled = false;
            confirmAddBtn.textContent = 'Link';
            if (addError) { addError.textContent = 'Network error: ' + err.message; addError.style.display = 'block'; }
          });
      });
    }

    // Remove dataset buttons
    document.querySelectorAll('.remove-dataset-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var versionId = btn.dataset.versionId;
        var removeUrl = btn.dataset.removeUrl;
        if (!removeUrl) return;
        if (!confirm('Remove this dataset from the dashboard?')) return;
        btn.disabled = true;

        fetch(removeUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: '{}',
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.success) {
              var card = document.getElementById('dataset-card-' + versionId);
              if (card) {
                card.style.transition = 'opacity 0.2s';
                card.style.opacity = '0';
                setTimeout(function () { card.remove(); }, 200);
              }
              showToast('Dataset removed', 'success');
            } else {
              btn.disabled = false;
              showToast(data.error || 'Could not remove dataset', 'error');
            }
          })
          .catch(function (err) {
            btn.disabled = false;
            showToast('Network error: ' + err.message, 'error');
          });
      });
    });
  }

  // ── Interactive Dashboard Filters ─────────────────────────────────────────

  var _filterState = {};          // column → current value(s)
  var _filterColumnMeta = {};     // column → {type, unique_values?, min?, max?}
  var _filterApplyTimer = null;   // debounce timer
  var _filterConfig = [];         // from dashboard-filter-config script tag

  function _loadFilterConfig() {
    var el = document.getElementById('dashboard-filter-config');
    if (!el) return [];
    try { return JSON.parse(el.textContent) || []; } catch (_) { return []; }
  }

  // Build _filterColumnMeta from the columnsUrl response (which already has unique_values + range_info)
  function _buildMetaFromColumnsResponse(data) {
    _filterColumnMeta = {};
    var uv = data.unique_values || {};
    var ri = data.range_info || {};
    (data.dimensions || []).forEach(function (col) {
      _filterColumnMeta[col] = { column: col, type: 'categorical', unique_values: uv[col] || [] };
    });
    (data.measures || []).forEach(function (col) {
      var r = ri[col] || {};
      _filterColumnMeta[col] = { column: col, type: 'numeric', min: r.min !== undefined ? r.min : 0, max: r.max !== undefined ? r.max : 100 };
    });
  }

  // Fetch column metadata using the existing columnsUrl (returns unique_values + range_info)
  function _fetchFilterColumns(callback) {
    var cfg = getApiConfig();
    if (!cfg || !cfg.columnsUrl) return;
    fetch(cfg.columnsUrl, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        _buildMetaFromColumnsResponse(data);
        if (typeof callback === 'function') callback(data);
      })
      .catch(function () {});
  }

  // Populate the filter controls with options from column metadata
  function _populateFilterControls() {
    _filterConfig.forEach(function (f) {
      var meta = _filterColumnMeta[f.column];
      if (!meta) return;  // column not found in dataset
      var item = document.querySelector('.filter-control-item[data-filter-id="' + CSS.escape(f.id) + '"]');
      if (!item) return;

      var vals = meta.unique_values || [];

      if (f.filter_type === 'dropdown') {
        var sel = item.querySelector('.filter-dropdown');
        if (!sel) return;
        while (sel.options.length > 1) sel.remove(1);
        vals.forEach(function (v) {
          var opt = document.createElement('option');
          opt.value = v; opt.textContent = v;
          sel.appendChild(opt);
        });
        sel.addEventListener('change', function () {
          _filterState[f.column] = sel.value;
          _scheduleFilterApply();
        });
      } else if (f.filter_type === 'radio') {
        var rg = item.querySelector('.filter-radio-group');
        if (!rg) return;
        rg.querySelectorAll('.filter-radio-option:not(:first-child)').forEach(function (el) { el.remove(); });
        vals.slice(0, 12).forEach(function (v) {
          var lbl = document.createElement('label');
          lbl.className = 'filter-radio-option';
          var inp = document.createElement('input');
          inp.type = 'radio';
          inp.name = 'filter_' + f.id;
          inp.value = v;
          inp.className = 'sr-only';
          var span = document.createElement('span');
          span.className = 'inline-flex items-center rounded-full border border-violet-300 bg-white px-2.5 py-1 text-xs font-medium text-violet-700 cursor-pointer transition-all hover:border-violet-500';
          span.textContent = v;
          lbl.appendChild(inp);
          lbl.appendChild(span);
          rg.appendChild(lbl);
        });
        rg.querySelectorAll('input[type="radio"]').forEach(function (radio) {
          radio.addEventListener('change', function () {
            _updateRadioVisuals(rg);
            _filterState[f.column] = radio.value;
            _scheduleFilterApply();
          });
        });
      } else if (f.filter_type === 'multiselect') {
        var ms = item.querySelector('.filter-multiselect');
        if (!ms) return;
        while (ms.options.length > 1) ms.remove(1);
        vals.forEach(function (v) {
          var opt = document.createElement('option');
          opt.value = v; opt.textContent = v;
          ms.appendChild(opt);
        });
        ms.addEventListener('change', function () {
          var selected = [];
          for (var i = 0; i < ms.options.length; i++) {
            if (ms.options[i].selected && ms.options[i].value !== '__all__') selected.push(ms.options[i].value);
          }
          _filterState[f.column] = selected;
          _scheduleFilterApply();
        });
      } else if (f.filter_type === 'range') {
        var slider = item.querySelector('.filter-range-input');
        var valLabel = item.querySelector('.filter-range-value');
        if (!slider) return;
        var lo = meta.min !== undefined ? meta.min : 0;
        var hi = meta.max !== undefined ? meta.max : 100;
        slider.min = lo;
        slider.max = hi;
        slider.step = hi > 1000 ? Math.round((hi - lo) / 100) || 1 : 1;
        slider.value = hi;  // start at max (show all)
        if (valLabel) valLabel.textContent = Number(lo).toLocaleString() + ' – ' + Number(hi).toLocaleString();
        slider.addEventListener('input', function () {
          var curHi = Number(slider.value);
          if (valLabel) valLabel.textContent = Number(lo).toLocaleString() + ' – ' + Number(curHi).toLocaleString();
          _filterState[f.column] = [lo, curHi];
          _scheduleFilterApply();
        });
      }
    });
  }

  function _updateRadioVisuals(rg) {
    rg.querySelectorAll('.filter-radio-option').forEach(function (lbl) {
      var inp = lbl.querySelector('input');
      var span = lbl.querySelector('span');
      if (!inp || !span) return;
      if (inp.checked) {
        span.style.background = '#7c3aed';
        span.style.color = '#fff';
        span.style.borderColor = '#7c3aed';
      } else {
        span.style.background = '';
        span.style.color = '';
        span.style.borderColor = '';
      }
    });
  }

  function _scheduleFilterApply() {
    if (_filterApplyTimer) clearTimeout(_filterApplyTimer);
    _filterApplyTimer = setTimeout(_applyFilters, 400);
  }

  function _buildFilterPayload() {
    var filters = [];
    _filterConfig.forEach(function (f) {
      var val = _filterState[f.column];
      if (val === undefined || val === '__all__' || val === '' || (Array.isArray(val) && val.length === 0)) return;
      filters.push({ column: f.column, filter_type: f.filter_type, value: val });
    });
    return filters;
  }

  function _updateFilterCountBadge() {
    var active = _buildFilterPayload().length;
    var badge = document.getElementById('active-filter-count');
    var resetBtn = document.getElementById('reset-filters-btn');
    var toolbarBadge = document.getElementById('toolbar-filter-count');
    if (badge) {
      if (active > 0) {
        badge.textContent = active + ' active';
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }
    if (resetBtn) {
      if (active > 0) resetBtn.classList.remove('hidden');
      else resetBtn.classList.add('hidden');
    }
    if (toolbarBadge) {
      if (active > 0) {
        toolbarBadge.textContent = active + ' active';
        toolbarBadge.classList.remove('hidden');
      } else if (_filterConfig.length > 0) {
        toolbarBadge.textContent = _filterConfig.length;
        toolbarBadge.classList.remove('hidden');
      } else {
        toolbarBadge.classList.add('hidden');
      }
    }
  }

  function _applyFilters() {
    var cfg = getApiConfig();
    if (!cfg || !cfg.applyFiltersUrl) return;

    var filters = _buildFilterPayload();
    _updateFilterCountBadge();

    var loading = document.getElementById('filter-loading');
    if (loading) loading.style.display = 'flex';

    fetch(cfg.applyFiltersUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({ filters: filters }),
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (loading) loading.style.display = 'none';
        if (!data || !data.success) return;
        _updateChartsFromFilterResult(data.widgets);
      })
      .catch(function () {
        if (loading) loading.style.display = 'none';
      });
  }

  function _updateChartsFromFilterResult(widgetsMap) {
    if (!widgetsMap) return;
    Object.keys(widgetsMap).forEach(function (widgetId) {
      var newConfig = widgetsMap[widgetId];
      var entry = widgetCharts[widgetId];

      // Handle KPI widget update
      if (newConfig && newConfig.kpi) {
        var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');
        if (card) {
          var kpiVal = card.querySelector('.widget-kpi-value');
          if (kpiVal) kpiVal.textContent = newConfig.value || '–';
        }
        return;
      }

      // Handle table widget update
      if (newConfig && Array.isArray(newConfig.columns) && Array.isArray(newConfig.rows)) {
        var tableCard = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');
        if (tableCard) {
          var tbody = tableCard.querySelector('.widget-table-wrap tbody');
          if (tbody) {
            tbody.innerHTML = newConfig.rows.map(function (row) {
              return '<tr class="border-t border-slate-100 hover:bg-slate-50 transition-colors">' +
                row.map(function (cell) {
                  return '<td class="px-3 py-2 text-slate-700 whitespace-nowrap">' + escapeHtml(String(cell)) + '</td>';
                }).join('') + '</tr>';
            }).join('');
            var si = tableCard.querySelector('.table-search-input');
            if (si) si.value = '';
          }
        }
        return;
      }

      if (!entry || !entry.chart || !entry.canvas) return;
      if (!newConfig || !newConfig.data) return;

      try {
        // Inject tooltip callback into new config
        _injectTooltipCallback(newConfig);
        // Update chart data in place for smooth animation
        var chart = entry.chart;
        chart.data.labels = newConfig.data.labels || chart.data.labels;
        if (newConfig.data.datasets) {
          newConfig.data.datasets.forEach(function (ds, i) {
            if (chart.data.datasets[i]) {
              chart.data.datasets[i].data = ds.data;
            }
          });
        }
        chart.update('active');
      } catch (e) {
        console.warn('DashAI: filter chart update error', e);
      }
    });
  }

  // Bind all filter control change events
  function _bindFilterControlEvents() {
    _filterConfig.forEach(function (f) {
      var item = document.querySelector('.filter-control-item[data-filter-id="' + f.id + '"]');
      if (!item) return;

      if (f.filter_type === 'dropdown') {
        var sel = item.querySelector('.filter-dropdown');
        if (sel) {
          sel.addEventListener('change', function () {
            _filterState[f.column] = sel.value;
            _scheduleFilterApply();
          });
        }
      } else if (f.filter_type === 'multiselect') {
        var ms = item.querySelector('.filter-multiselect');
        if (ms) {
          ms.addEventListener('change', function () {
            var vals = [];
            for (var i = 0; i < ms.options.length; i++) {
              if (ms.options[i].selected && ms.options[i].value !== '__all__') vals.push(ms.options[i].value);
            }
            _filterState[f.column] = vals;
            _scheduleFilterApply();
          });
        }
      } else if (f.filter_type === 'range') {
        var slider = item.querySelector('.filter-range-input');
        var valLabel = item.querySelector('.filter-range-value');
        if (slider) {
          slider.addEventListener('input', function () {
            var meta = _filterColumnMeta[f.column] || {};
            var lo = meta.min !== undefined ? Number(meta.min) : 0;
            var hi = Number(slider.value);
            if (valLabel) valLabel.textContent = Number(lo).toLocaleString() + ' – ' + Number(hi).toLocaleString();
            _filterState[f.column] = [lo, hi];
            _scheduleFilterApply();
          });
        }
      }
      // Radio buttons are bound in _populateFilterControls
    });
  }

  // ── Filter Manager Modal ────────────────────────────────────────────────

  function initFilterManager() {
    var cfg = getApiConfig();
    if (!cfg) return;

    _filterConfig = _loadFilterConfig();

    var openBtn1 = document.getElementById('open-filter-manager-btn');
    var overlay = document.getElementById('filter-manager-overlay');
    var modal = document.getElementById('filter-manager-modal');
    var closeBtn = document.getElementById('close-filter-manager-btn');
    var cancelBtn = document.getElementById('cancel-filter-manager-btn');
    var saveBtn = document.getElementById('save-filter-manager-btn');
    var fmColumn = document.getElementById('fm-column');
    var fmType = document.getElementById('fm-type');
    var fmLabel = document.getElementById('fm-label');
    var fmAddBtn = document.getElementById('fm-add-btn');
    var fmList = document.getElementById('fm-filter-list');
    var fmNoFilters = document.getElementById('fm-no-filters');
    var fmAddError = document.getElementById('fm-add-error');
    var fmError = document.getElementById('fm-error');

    if (!modal) return;

    var pendingFilters = [];  // local copy being edited

    function openModal() {
      pendingFilters = JSON.parse(JSON.stringify(_filterConfig));
      renderFmList();
      modal.style.display = 'block';
      if (overlay) overlay.style.display = 'block';
      // Load columns for the column picker
      if (fmColumn) {
        fmColumn.innerHTML = '<option value="">— loading… —</option>';
        _fetchFilterColumns(function (data) {
          fmColumn.innerHTML = '<option value="">— select column —</option>';
          var allCols = (data.dimensions || []).concat(data.measures || []);
          _syncAddFilterTypeOptions('', fmType.value || 'dropdown');
          allCols.forEach(function (col) {
            var meta = _filterColumnMeta[col] || {};
            var opt = document.createElement('option');
            opt.value = col;
            opt.textContent = col + (meta.type === 'numeric' ? ' (numeric)' : ' (' + (meta.unique_values || []).length + ' values)');
            fmColumn.appendChild(opt);
          });
          if (allCols.length === 0) {
            fmColumn.innerHTML = '<option value="">No columns available</option>';
          }
          // Re-render once metadata is loaded so saved filters show accurate type options.
          renderFmList();
        });
        // Update type options based on column type (bind once)
        if (!fmColumn._changeListenerBound) {
          fmColumn._changeListenerBound = true;
          fmColumn.addEventListener('change', function () {
            _syncAddFilterTypeOptions(fmColumn.value, fmType.value);
            if (!fmLabel.value && fmColumn.value) fmLabel.value = fmColumn.value;
          });
        }
      }
    }

    function closeModal() {
      modal.style.display = 'none';
      if (overlay) overlay.style.display = 'none';
    }

    var TYPE_LABELS = { dropdown: 'Dropdown', radio: 'Radio', multiselect: 'Multi-select', range: 'Range slider' };
    var CATEGORICAL_TYPES = ['dropdown', 'radio', 'multiselect'];

    function _allowedFilterTypesForColumn(column) {
      var meta = _filterColumnMeta[column] || {};
      if (meta.type === 'numeric') return ['range'];
      if (meta.type === 'categorical') return CATEGORICAL_TYPES.slice();
      // Column metadata may not be loaded yet (e.g., when modal first opens).
      // In that case keep all options visible and avoid coercing saved values.
      return ['dropdown', 'radio', 'multiselect', 'range'];
    }

    function _renderFilterTypeSelectOptions(types, selected) {
      return types.map(function (t) {
        var sel = selected === t ? ' selected' : '';
        return '<option value="' + t + '"' + sel + '>' + TYPE_LABELS[t] + '</option>';
      }).join('');
    }

    function _syncAddFilterTypeOptions(column, preferredType) {
      if (!fmType) return;
      var allowed = _allowedFilterTypesForColumn(column);
      fmType.innerHTML = _renderFilterTypeSelectOptions(allowed, preferredType);
      if (!allowed.includes(fmType.value)) {
        fmType.value = allowed[0] || 'dropdown';
      }
    }

    function renderFmList() {
      if (!fmList) return;
      fmList.querySelectorAll('.fm-filter-row').forEach(function (r) { r.remove(); });
      if (pendingFilters.length === 0) {
        if (fmNoFilters) fmNoFilters.style.display = '';
        return;
      }
      if (fmNoFilters) fmNoFilters.style.display = 'none';
      pendingFilters.forEach(function (f, idx) {
        var allowedTypes = _allowedFilterTypesForColumn(f.column);
        if (!allowedTypes.includes(f.filter_type)) {
          f.filter_type = allowedTypes[0] || 'dropdown';
        }
        var typeOpts = _renderFilterTypeSelectOptions(allowedTypes, f.filter_type);

        var row = document.createElement('div');
        row.className = 'fm-filter-row rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm space-y-2';
        row.innerHTML = [
          '<div class="flex items-center gap-2">',
          '  <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-violet-100 text-violet-700">',
          '    <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707L13 13.414V19a1 1 0 01-.553.894l-4 2A1 1 0 017 21v-7.586L3.293 6.707A1 1 0 013 6V4z"/></svg>',
          '  </div>',
          '  <span class="text-xs font-semibold text-slate-500 truncate flex-1">' + escapeHtml(f.column) + '</span>',
          '  <button class="fm-remove-btn ml-auto flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:bg-red-50 hover:text-red-500" data-idx="' + idx + '" title="Remove">',
          '    <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>',
          '  </button>',
          '</div>',
          '<div class="grid grid-cols-2 gap-2">',
          '  <div>',
          '    <label class="block text-[10px] font-semibold text-slate-500 mb-0.5">Label</label>',
          '    <input type="text" class="fm-edit-label w-full rounded-md border border-slate-300 px-2 py-1 text-xs focus:border-violet-500 focus:outline-none" value="' + escapeHtml(f.label || f.column) + '" data-idx="' + idx + '">',
          '  </div>',
          '  <div>',
          '    <label class="block text-[10px] font-semibold text-slate-500 mb-0.5">Type</label>',
          '    <select class="fm-edit-type w-full rounded-md border border-slate-300 px-2 py-1 text-xs focus:border-violet-500 focus:outline-none" data-idx="' + idx + '">' + typeOpts + '</select>',
          '  </div>',
          '</div>',
        ].join('');
        fmList.insertBefore(row, fmNoFilters);
      });

      // Wire up remove buttons
      fmList.querySelectorAll('.fm-remove-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
          pendingFilters.splice(parseInt(btn.dataset.idx), 1);
          renderFmList();
        });
      });
      // Wire up inline label edit
      fmList.querySelectorAll('.fm-edit-label').forEach(function (inp) {
        inp.addEventListener('input', function () {
          pendingFilters[parseInt(inp.dataset.idx)].label = inp.value;
        });
      });
      // Wire up inline type edit
      fmList.querySelectorAll('.fm-edit-type').forEach(function (sel) {
        sel.addEventListener('change', function () {
          pendingFilters[parseInt(sel.dataset.idx)].filter_type = sel.value;
        });
      });
    }

    if (openBtn1) openBtn1.addEventListener('click', openModal);
    if (overlay) overlay.addEventListener('click', closeModal);
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

    if (fmAddBtn && fmColumn && fmType && fmLabel) {
      fmAddBtn.addEventListener('click', function () {
        var col = fmColumn.value;
        var ftype = fmType.value;
        var label = fmLabel.value.trim() || col;
        if (!col) {
          if (fmAddError) { fmAddError.textContent = 'Please select a column.'; fmAddError.style.display = ''; }
          return;
        }
        var allowedTypes = _allowedFilterTypesForColumn(col);
        if (!allowedTypes.includes(ftype)) {
          ftype = allowedTypes[0] || 'dropdown';
          _syncAddFilterTypeOptions(col, ftype);
        }
        if (fmAddError) fmAddError.style.display = 'none';
        var id = col + '_' + Date.now();
        pendingFilters.push({ id: id, column: col, filter_type: ftype, label: label });
        fmColumn.value = '';
        fmLabel.value = '';
        renderFmList();
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        if (!cfg.saveFiltersUrl) return;
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving…';
        if (fmError) fmError.style.display = 'none';
        fetch(cfg.saveFiltersUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
          body: JSON.stringify({ filters: pendingFilters }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Filters';
            if (data.success) {
              // Update in-memory config so reopening the modal shows correct filters
              _filterConfig = data.filters || pendingFilters;
              showToast('Filters saved — reloading…', 'success');
              setTimeout(function () { window.location.reload(); }, 600);
            } else {
              if (fmError) { fmError.textContent = data.error || 'Failed to save filters.'; fmError.style.display = ''; }
            }
          })
          .catch(function (err) {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Filters';
            if (fmError) { fmError.textContent = 'Network error: ' + err.message; fmError.style.display = ''; }
          });
      });
    }

    // Reset all filters button
    var resetBtn = document.getElementById('reset-filters-btn');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        _filterState = {};
        // Reset all controls
        document.querySelectorAll('.filter-dropdown').forEach(function (sel) { sel.value = '__all__'; });
        document.querySelectorAll('.filter-radio-group').forEach(function (rg) {
          var first = rg.querySelector('input[type="radio"]');
          if (first) { first.checked = true; _updateRadioVisuals(rg); }
        });
        document.querySelectorAll('.filter-multiselect').forEach(function (ms) {
          for (var i = 0; i < ms.options.length; i++) {
            ms.options[i].selected = (ms.options[i].value === '__all__');
          }
        });
        _applyFilters();
      });
    }

    // Initialize filter panel if filters exist
    if (_filterConfig.length > 0) {
      _fetchFilterColumns(function () {
        _populateFilterControls();
      });
    }
  }

  // ── Dashboard Panel Modals (Datasets · Shares · Filters) ─────────────────

  function initDashboardPanelModals() {
    // Generic open/close helper
    function makeModal(openBtnId, closeBtnId, overlayId, modalId) {
      var openBtn = document.getElementById(openBtnId);
      var closeBtn = document.getElementById(closeBtnId);
      var overlay = document.getElementById(overlayId);
      var modal = document.getElementById(modalId);
      if (!modal) return null;

      function open() {
        modal.style.display = 'block';
        if (overlay) overlay.style.display = 'block';
      }
      function close() {
        modal.style.display = 'none';
        if (overlay) overlay.style.display = 'none';
      }

      if (openBtn) openBtn.addEventListener('click', open);
      if (closeBtn) closeBtn.addEventListener('click', close);
      if (overlay) overlay.addEventListener('click', close);
      return { open: open, close: close };
    }

    var datasetsM = makeModal('open-datasets-modal-btn', 'close-datasets-modal-btn', 'datasets-modal-overlay', 'datasets-modal');
    var sharesM   = makeModal('open-shares-modal-btn',   'close-shares-modal-btn',   'shares-modal-overlay',   'shares-modal');
    var filtersM  = makeModal('open-filters-modal-btn',  'close-filters-modal-btn',  'filters-modal-overlay',  'filters-modal');

    // Close panel modals on Escape (chart builder handles its own Escape)
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      if (datasetsM) datasetsM.close();
      if (sharesM)   sharesM.close();
      if (filtersM)  filtersM.close();
    });
  }

  // ── Init ─────────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    renderPaletteDots();
    renderHomeChart();
    renderWidgetCharts();
    initCopyButtons();
    initDragDrop();
    initSidebar();
    initWidgetDelete();
    initWidgetRename();
    initMaximize();
    initDownloadButtons();
    initDashboardPdfExport();
    initChartBuilder();
    initWidgetDragResize();
    initWidgetWidthToggle();
    initWidgetDragOrder();
    initWidgetEdit();
    initDashboardRename();
    initHeadingBuilder();
    initSectionEdit();
    initDatasetsPanel();
    initPresentationMode();
    initTextCanvasBuilder();
    initTableInteractions();
    initInsertZones();
    initFilterManager();
    initDashboardPanelModals();
  });

})();
