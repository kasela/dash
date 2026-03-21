(function () {
  'use strict';

  // ── Utilities ─────────────────────────────────────────────────────────────────

  function getCsrfToken() {
    // 1. Try the embedded JSON config (dashboard detail page)
    var el = document.getElementById('dashboard-api-urls');
    if (el) {
      try {
        var cfg = JSON.parse(el.textContent);
        if (cfg.csrfToken) return cfg.csrfToken;
      } catch (_) {}
    }
    // 2. Fall back to reading the csrftoken cookie
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  function getApiConfig() {
    var el = document.getElementById('dashboard-api-urls');
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (_) { return null; }
  }

  // ── Chart rendering ──────────────────────────────────────────────────────────

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

  function renderWidgetCharts() {
    document.querySelectorAll('[data-widget-chart]').forEach(function (wrap) {
      var canvas = wrap.querySelector('canvas');
      if (!canvas || typeof Chart === 'undefined') return;
      try {
        var cfg = JSON.parse(wrap.dataset.widgetChart);
        if (!cfg.options) cfg.options = {};
        cfg.options.responsive = true;
        cfg.options.maintainAspectRatio = false;
        new Chart(canvas, cfg);
      } catch (e) {
        console.warn('DashAI: widget chart error', e);
        var errEl = wrap.querySelector('.chart-error');
        if (errEl) errEl.hidden = false;
      }
    });
  }

  // ── Clipboard copy ────────────────────────────────────────────────────────────

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

  // ── Drag & Drop file upload ───────────────────────────────────────────────────

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

  // ── Mobile sidebar toggle ────────────────────────────────────────────────────

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

  // ── Toast notifications ──────────────────────────────────────────────────────

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

  // ── Widget deletion ──────────────────────────────────────────────────────────

  function doDeleteWidget(btn) {
    var widgetId = btn.dataset.widgetId;
    var deleteUrl = btn.dataset.deleteUrl;
    var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');

    btn.disabled = true;
    btn.classList.add('opacity-50');

    fetch(deleteUrl, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'Content-Type': 'application/json',
      },
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
        console.error('DashAI delete error:', err);
      });
  }

  function initWidgetDelete() {
    document.querySelectorAll('.delete-widget-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        // Inline confirm via a small overlay on the card itself
        var widgetId = btn.dataset.widgetId;
        var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');
        if (!card) return;

        // If a confirm row already exists on this card, skip
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

  // ── Chart Builder Modal ──────────────────────────────────────────────────────

  var cbPreviewChart = null;

  function openChartBuilder() {
    var modal = document.getElementById('chart-builder-modal');
    var overlay = document.getElementById('chart-builder-overlay');
    if (!modal) return;

    // Show modal
    modal.style.display = 'flex';
    if (overlay) overlay.style.display = 'block';

    // Reset to loading state
    document.getElementById('cb-loading').style.display = 'flex';
    document.getElementById('cb-error').style.display = 'none';
    document.getElementById('cb-form').style.display = 'none';
    document.getElementById('cb-preview-btn').style.display = 'none';
    document.getElementById('cb-submit-btn').style.display = 'none';
    document.getElementById('cb-preview-wrap').style.display = 'none';
    var valErr = document.getElementById('cb-validation-error');
    if (valErr) valErr.style.display = 'none';

    // Reset form fields
    var titleInput = document.getElementById('cb-title');
    if (titleInput) titleInput.value = '';
    destroyCbPreview();

    var cfg = getApiConfig();
    if (!cfg || !cfg.columnsUrl) {
      showCbError('Configuration error: dashboard API URLs not found.');
      return;
    }

    fetch(cfg.columnsUrl, { credentials: 'same-origin' })
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
        updateCbFieldVisibility();
      })
      .catch(function (err) {
        console.error('DashAI: columns fetch failed', err);
        showCbError('Could not load dataset columns (' + err.message + '). Make sure the dataset file is still accessible.');
      });
  }

  function closeChartBuilder() {
    var modal = document.getElementById('chart-builder-modal');
    var overlay = document.getElementById('chart-builder-overlay');
    if (modal) modal.style.display = 'none';
    if (overlay) overlay.style.display = 'none';
    destroyCbPreview();
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

    dimSel.innerHTML = '<option value="">— select column —</option>';
    measureSel.innerHTML = '<option value="">— select column —</option>';

    // Dimension: prefer categorical cols, fall back to all cols
    var dimCols = dimensions.length > 0 ? dimensions : allCols;
    dimCols.forEach(function (col) {
      var opt = document.createElement('option');
      opt.value = col;
      opt.textContent = col;
      dimSel.appendChild(opt);
    });

    // Measure: numeric cols only
    measures.forEach(function (col) {
      var opt = document.createElement('option');
      opt.value = col;
      opt.textContent = col;
      measureSel.appendChild(opt);
    });

    // Auto-select first available
    if (dimCols.length > 0) dimSel.value = dimCols[0];
    if (measures.length > 0) measureSel.value = measures[0];

    autoSetTitle();

    if (dimCols.length === 0 && measures.length === 0) {
      showCbValidationError('No columns found in this dataset. Try re-uploading the file.');
    }
  }

  function getSelectedChartType() {
    var checked = document.querySelector('input[name="cb_chart_type"]:checked');
    return checked ? checked.value : 'bar';
  }

  function autoSetTitle() {
    var titleInput = document.getElementById('cb-title');
    if (!titleInput || titleInput.value.trim()) return;
    var type = getSelectedChartType();
    var dim = (document.getElementById('cb-dimension') || {}).value || '';
    var measure = (document.getElementById('cb-measure') || {}).value || '';
    if (type === 'kpi' && measure) titleInput.value = 'Total ' + measure;
    else if (type === 'pie' && dim) titleInput.value = 'Distribution: ' + dim;
    else if (dim && measure) titleInput.value = measure + ' by ' + dim;
  }

  function updateCbFieldVisibility() {
    var type = getSelectedChartType();
    var dimWrap = document.getElementById('cb-dimension-wrap');
    var measureWrap = document.getElementById('cb-measure-wrap');

    if (type === 'kpi') {
      dimWrap.style.display = 'none';
      measureWrap.style.display = '';
    } else {
      dimWrap.style.display = '';
      measureWrap.style.display = '';
    }

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

    document.getElementById('cb-title').value = '';
    autoSetTitle();
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

  function validateCbForm() {
    var type = getSelectedChartType();
    var dim = (document.getElementById('cb-dimension') || {}).value || '';
    var measure = (document.getElementById('cb-measure') || {}).value || '';

    if (type === 'kpi' && !measure) {
      showCbValidationError('Select a measure column for the KPI.');
      return false;
    }
    if ((type === 'bar' || type === 'line') && (!dim || !measure)) {
      showCbValidationError('Select both a dimension and a measure column.');
      return false;
    }
    if (type === 'pie' && !dim) {
      showCbValidationError('Select a dimension column for the pie chart.');
      return false;
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
      try {
        var ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      } catch (_) {}
    }
    var kpiEl = document.getElementById('cb-preview-kpi');
    if (kpiEl) { kpiEl.style.display = 'none'; kpiEl.textContent = ''; }
  }

  function buildPayload(previewOnly) {
    return {
      chart_type: getSelectedChartType(),
      title: (document.getElementById('cb-title').value || '').trim() || 'New Widget',
      dimension: (document.getElementById('cb-dimension') || {}).value || '',
      measure: (document.getElementById('cb-measure') || {}).value || '',
      preview_only: !!previewOnly,
    };
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
        console.error('DashAI preview error:', err);
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

    canvas.style.display = '';
    if (kpiEl) kpiEl.style.display = 'none';

    if (!config || typeof Chart === 'undefined') return;
    try {
      var chartCfg = JSON.parse(JSON.stringify(config));
      if (!chartCfg.options) chartCfg.options = {};
      chartCfg.options.responsive = true;
      chartCfg.options.maintainAspectRatio = false;
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

    fetch(cfg.addWidgetUrl, {
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
        submitBtn.textContent = 'Add to Dashboard';
        submitBtn.disabled = false;
        if (data.success) {
          closeChartBuilder();
          showToast('Chart added — reloading…', 'success');
          setTimeout(function () { window.location.reload(); }, 700);
        } else {
          showCbValidationError(data.error || 'Failed to add widget. Please try again.');
        }
      })
      .catch(function (err) {
        submitBtn.textContent = 'Add to Dashboard';
        submitBtn.disabled = false;
        showCbValidationError('Network error: ' + err.message);
        console.error('DashAI submit error:', err);
      });
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

    var dimSel = document.getElementById('cb-dimension');
    var measureSel = document.getElementById('cb-measure');
    if (dimSel) dimSel.addEventListener('change', function () {
      document.getElementById('cb-title').value = '';
      autoSetTitle();
    });
    if (measureSel) measureSel.addEventListener('change', function () {
      document.getElementById('cb-title').value = '';
      autoSetTitle();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeChartBuilder();
    });
  }

  // ── Init ─────────────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    renderHomeChart();
    renderWidgetCharts();
    initCopyButtons();
    initDragDrop();
    initSidebar();
    initWidgetDelete();
    initChartBuilder();
  });

})();
