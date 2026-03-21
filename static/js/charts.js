(function () {
  const el = document.getElementById("chart-config");
  const canvas = document.getElementById("home-chart");
  if (!el || !canvas || typeof Chart === "undefined") {
    return;
  }

  const config = JSON.parse(el.textContent);
  // eslint-disable-next-line no-new
  new Chart(canvas, config);
})();
