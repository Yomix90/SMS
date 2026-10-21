/* Graphiques Chart.js partagés (couleurs adaptées au thème clair / sombre). */
(function () {
  "use strict";
  var COLORS = { sent: "#2563eb", delivered: "#10b981", failed: "#ef4444", received: "#8b5cf6",
                 queued: "#94a3b8", blocked: "#475569", canceled: "#64748b", scheduled: "#0ea5e9",
                 dispatched: "#6366f1", paused: "#f59e0b" };
  var charts = [];

  function themed() {
    var dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    Chart.defaults.color = dark ? "#9ca3af" : "#6b7280";
    Chart.defaults.borderColor = dark ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.06)";
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  }

  function make(id, config) {
    var el = document.getElementById(id);
    if (!el || !window.Chart) return null;
    themed();
    var chart = new Chart(el, config);
    charts.push(chart);
    return chart;
  }

  document.addEventListener("themechange", function () { themed(); charts.forEach(function (c) { c.update(); }); });

  var rtl = document.documentElement.dir === "rtl";

  window.AppCharts = {
    colors: COLORS,
    activity: function (id, data, labels) {
      return make(id, {
        type: "line",
        data: {
          labels: data.labels,
          datasets: ["sent", "delivered", "failed", "received"].map(function (k) {
            return { label: labels[k], data: data[k], borderColor: COLORS[k], backgroundColor: COLORS[k] + "22",
                     tension: .3, fill: k === "sent", pointRadius: 2, borderWidth: 2 };
          })
        },
        options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
                   scales: { x: { reverse: rtl }, y: { beginAtZero: true, ticks: { precision: 0 } } },
                   plugins: { legend: { position: "bottom", rtl: rtl } } }
      });
    },
    doughnut: function (id, entries) {
      return make(id, {
        type: "doughnut",
        data: { labels: entries.map(function (e) { return e.label; }),
                datasets: [{ data: entries.map(function (e) { return e.value; }),
                             backgroundColor: entries.map(function (e) { return COLORS[e.key] || "#94a3b8"; }) }] },
        options: { maintainAspectRatio: false, cutout: "65%", plugins: { legend: { position: "bottom", rtl: rtl } } }
      });
    },
    bars: function (id, labels, series) {
      return make(id, {
        type: "bar",
        data: { labels: labels, datasets: series.map(function (s) {
          return { label: s.label, data: s.data, backgroundColor: COLORS[s.key] || "#2563eb", borderRadius: 6 };
        }) },
        options: { maintainAspectRatio: false, scales: { x: { reverse: rtl }, y: { beginAtZero: true, ticks: { precision: 0 } } },
                   plugins: { legend: { position: "bottom", rtl: rtl } } }
      });
    }
  };
})();
