(function () {
  'use strict';

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
        wrap.querySelector('.chart-error') && (wrap.querySelector('.chart-error').hidden = false);
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

  function initWidgetDelete() {
    document.querySelectorAll('.delete-widget-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var widgetId = btn.dataset.widgetId;
        var deleteUrl = btn.dataset.deleteUrl;
        var card = document.querySelector('.widget-card[data-widget-id="' + widgetId + '"]');

        if (!confirm('Delete this widget?')) return;

        var apiUrls = getApiUrls();
        if (!apiUrls) return;

        fetch(deleteUrl, {
          method: 'POST',
          headers: { 'X-CSRFToken': apiUrls.csrfToken, 'Content-Type': 'application/json' },
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.success) {
              if (card) card.remove();
              updateWidgetCount(-1);
              showToast('Widget deleted', 'success');
            } else {
              showToast('Failed to delete widget', 'error');
            }
          })
          .catch(function () {
            showToast('Network error – could not delete widget', 'error');
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

    // Toggle empty state
    var grid = document.getElementById('widgets-grid');
    var empty = document.getElementById('empty-state');
    if (!grid || !empty) return;
    var remaining = grid.querySelectorAll('.widget-card').length;
    empty.classList.toggle('hidden', remaining > 0);
  }

  // ── Chart Builder Modal ──────────────────────────────────────────────────────

  var cbColumns = { dimensions: [], measures: [], date_cols: [], all_cols: [] };
  var cbPreviewChart = null;

  function getApiUrls() {
    var el = document.getElementById('dashboard-api-urls');
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function openChartBuilder() {
    var modal = document.getElementById('chart-builder-modal');
    var overlay = document.getElementById('chart-builder-overlay');
    if (!modal) return;

    modal.classList.remove('hidden');
    modal.classList.add('flex');
    if (overlay) overlay.classList.remove('hidden');

    // Show loading, hide everything else
    document.getElementById('cb-loading').classList.remove('hidden');
    document.getElementById('cb-error').classList.add('hidden');
    document.getElementById('cb-form').classList.add('hidden');
    document.getElementById('cb-preview-btn').classList.add('hidden');
    document.getElementById('cb-submit-btn').classList.add('hidden');

    var apiUrls = getApiUrls();
    if (!apiUrls) {
      showCbError('Configuration error: API URLs not found.');
      return;
    }

    fetch(apiUrls.columnsUrl)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        cbColumns = data;
        populateCbForm(data);
        document.getElementById('cb-loading').classList.add('hidden');
        document.getElementById('cb-form').classList.remove('hidden');
        document.getElementById('cb-preview-btn').classList.remove('hidden');
        document.getElementById('cb-submit-btn').classList.remove('hidden');
        updateCbFieldVisibility();
      })
      .catch(function () {
        showCbError('Failed to load dataset columns. Please try again.');
      });
  }

  function closeChartBuilder() {
    var modal = document.getElementById('chart-builder-modal');
    var overlay = document.getElementById('chart-builder-overlay');
    if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
    if (overlay) overlay.classList.add('hidden');
    destroyCbPreview();
  }

  function showCbError(msg) {
    var el = document.getElementById('cb-error');
    document.getElementById('cb-loading').classList.add('hidden');
    el.textContent = msg;
    el.classList.remove('hidden');
  }

  function populateCbForm(data) {
    var dimSel = document.getElementById('cb-dimension');
    var measureSel = document.getElementById('cb-measure');

    // Reset options
    dimSel.innerHTML = '<option value="">— select column —</option>';
    measureSel.innerHTML = '<option value="">— select column —</option>';

    // Populate dimension: all non-measure columns
    var dimCols = data.dimensions.length > 0 ? data.dimensions : data.all_cols;
    dimCols.forEach(function (col) {
      var opt = document.createElement('option');
      opt.value = col;
      opt.textContent = col;
      dimSel.appendChild(opt);
    });

    // Populate measure: numeric columns
    data.measures.forEach(function (col) {
      var opt = document.createElement('option');
      opt.value = col;
      opt.textContent = col;
      measureSel.appendChild(opt);
    });

    // Auto-select first options
    if (dimCols.length > 0) dimSel.value = dimCols[0];
    if (data.measures.length > 0) measureSel.value = data.measures[0];

    // Auto-set title
    autoSetTitle();
  }

  function getSelectedChartType() {
    var checked = document.querySelector('input[name="cb_chart_type"]:checked');
    return checked ? checked.value : 'bar';
  }

  function autoSetTitle() {
    var titleInput = document.getElementById('cb-title');
    if (!titleInput || titleInput.value.trim()) return; // don't overwrite user input
    var type = getSelectedChartType();
    var dim = document.getElementById('cb-dimension').value;
    var measure = document.getElementById('cb-measure').value;
    if (type === 'kpi' && measure) {
      titleInput.value = 'Total ' + measure;
    } else if (type === 'pie' && dim) {
      titleInput.value = 'Distribution: ' + dim;
    } else if (dim && measure) {
      titleInput.value = measure + ' by ' + dim;
    }
  }

  function updateCbFieldVisibility() {
    var type = getSelectedChartType();
    var dimWrap = document.getElementById('cb-dimension-wrap');
    var measureWrap = document.getElementById('cb-measure-wrap');

    if (type === 'kpi') {
      dimWrap.classList.add('hidden');
      measureWrap.classList.remove('hidden');
    } else if (type === 'pie') {
      dimWrap.classList.remove('hidden');
      measureWrap.classList.remove('hidden');
    } else {
      dimWrap.classList.remove('hidden');
      measureWrap.classList.remove('hidden');
    }

    // Reset title on type change
    document.getElementById('cb-title').value = '';
    autoSetTitle();
    destroyCbPreview();
    document.getElementById('cb-preview-wrap').classList.add('hidden');
    document.getElementById('cb-validation-error').classList.add('hidden');
  }

  function destroyCbPreview() {
    if (cbPreviewChart) {
      cbPreviewChart.destroy();
      cbPreviewChart = null;
    }
    var canvas = document.getElementById('cb-preview-canvas');
    if (canvas) {
      var ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }

  function validateCbForm() {
    var type = getSelectedChartType();
    var dim = document.getElementById('cb-dimension').value;
    var measure = document.getElementById('cb-measure').value;
    var errEl = document.getElementById('cb-validation-error');

    if (type === 'kpi' && !measure) {
      errEl.textContent = 'Please select a measure column for the KPI.';
      errEl.classList.remove('hidden');
      return false;
    }
    if ((type === 'bar' || type === 'line') && (!dim || !measure)) {
      errEl.textContent = 'Please select both a dimension and a measure column.';
      errEl.classList.remove('hidden');
      return false;
    }
    if (type === 'pie' && !dim) {
      errEl.textContent = 'Please select a dimension column for the pie chart.';
      errEl.classList.remove('hidden');
      return false;
    }

    errEl.classList.add('hidden');
    return true;
  }

  function previewChart() {
    if (!validateCbForm()) return;

    var type = getSelectedChartType();
    var dim = document.getElementById('cb-dimension').value;
    var measure = document.getElementById('cb-measure').value;
    var apiUrls = getApiUrls();
    if (!apiUrls) return;

    var previewBtn = document.getElementById('cb-preview-btn');
    previewBtn.textContent = 'Loading…';
    previewBtn.disabled = true;

    fetch(apiUrls.addWidgetUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': apiUrls.csrfToken,
      },
      body: JSON.stringify({
        chart_type: type,
        title: '__preview__',
        dimension: dim,
        measure: measure,
        preview_only: true,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        previewBtn.textContent = 'Preview';
        previewBtn.disabled = false;

        if (data.error) {
          document.getElementById('cb-validation-error').textContent = data.error;
          document.getElementById('cb-validation-error').classList.remove('hidden');
          return;
        }

        renderCbPreview(data.chart_config, type);
      })
      .catch(function () {
        previewBtn.textContent = 'Preview';
        previewBtn.disabled = false;
        showToast('Preview failed – network error', 'error');
      });
  }

  function renderCbPreview(config, type) {
    destroyCbPreview();

    var previewWrap = document.getElementById('cb-preview-wrap');
    var kpiEl = document.getElementById('cb-preview-kpi');
    var canvas = document.getElementById('cb-preview-canvas');

    previewWrap.classList.remove('hidden');

    if (type === 'kpi') {
      canvas.style.display = 'none';
      kpiEl.classList.remove('hidden');
      kpiEl.textContent = config.value || '–';
      return;
    }

    canvas.style.display = '';
    kpiEl.classList.add('hidden');

    if (!config || typeof Chart === 'undefined') return;
    try {
      var cfg = JSON.parse(JSON.stringify(config)); // deep clone
      if (!cfg.options) cfg.options = {};
      cfg.options.responsive = true;
      cfg.options.maintainAspectRatio = false;
      cbPreviewChart = new Chart(canvas, cfg);
    } catch (e) {
      console.warn('DashAI: preview chart error', e);
    }
  }

  function submitChartBuilder() {
    if (!validateCbForm()) return;

    var type = getSelectedChartType();
    var title = document.getElementById('cb-title').value.trim() || 'New Widget';
    var dim = document.getElementById('cb-dimension').value;
    var measure = document.getElementById('cb-measure').value;
    var apiUrls = getApiUrls();
    if (!apiUrls) return;

    var submitBtn = document.getElementById('cb-submit-btn');
    submitBtn.textContent = 'Adding…';
    submitBtn.disabled = true;

    fetch(apiUrls.addWidgetUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': apiUrls.csrfToken,
      },
      body: JSON.stringify({
        chart_type: type,
        title: title,
        dimension: dim,
        measure: measure,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        submitBtn.textContent = 'Add to Dashboard';
        submitBtn.disabled = false;

        if (data.success) {
          closeChartBuilder();
          showToast('Chart added! Refreshing…', 'success');
          setTimeout(function () { window.location.reload(); }, 800);
        } else {
          document.getElementById('cb-validation-error').textContent = data.error || 'Failed to add widget.';
          document.getElementById('cb-validation-error').classList.remove('hidden');
        }
      })
      .catch(function () {
        submitBtn.textContent = 'Add to Dashboard';
        submitBtn.disabled = false;
        showToast('Network error – could not add widget', 'error');
      });
  }

  function initChartBuilder() {
    var openBtn = document.getElementById('open-chart-builder-btn');
    var closeBtn = document.getElementById('close-chart-builder-btn');
    var overlay = document.getElementById('chart-builder-overlay');
    var previewBtn = document.getElementById('cb-preview-btn');
    var submitBtn = document.getElementById('cb-submit-btn');

    if (!openBtn) return; // Not on dashboard detail page

    openBtn.addEventListener('click', openChartBuilder);
    if (closeBtn) closeBtn.addEventListener('click', closeChartBuilder);
    if (overlay) overlay.addEventListener('click', closeChartBuilder);
    if (previewBtn) previewBtn.addEventListener('click', previewChart);
    if (submitBtn) submitBtn.addEventListener('click', submitChartBuilder);

    // Chart type change
    document.querySelectorAll('input[name="cb_chart_type"]').forEach(function (radio) {
      radio.addEventListener('change', updateCbFieldVisibility);
    });

    // Auto-update title when dimension/measure changes
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

    // Close on Escape
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
