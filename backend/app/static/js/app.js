/* Comportements communs du tableau de bord. */
(function () {
  "use strict";
  var APP = window.APP || {};
  var t = APP.i18n || {};
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content;

  // --- Requêtes AJAX avec jeton CSRF -------------------------------------------
  APP.post = function (url, data) {
    var body = data instanceof FormData ? data : new URLSearchParams(data || {});
    return fetch(url, { method: "POST", body: body, headers: { "X-CSRFToken": csrf, "X-Requested-With": "fetch" },
                        credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw j; return j; }); });
  };

  APP.postHtml = function (url, data) {
    return fetch(url, { method: "POST", body: data, headers: { "X-CSRFToken": csrf, "X-Requested-With": "fetch" },
                        credentials: "same-origin" })
      .then(function (r) { if (!r.ok || r.redirected) throw r; return r.text(); });
  };

  // --- Notifications toast -------------------------------------------------------
  APP.toast = function (title, body, variant) {
    var box = document.getElementById("toasts");
    if (!box || !window.bootstrap) return;
    var el = document.createElement("div");
    el.className = "toast align-items-center border-0 text-bg-" + (variant || "dark");
    el.setAttribute("role", "status");
    el.innerHTML = '<div class="d-flex"><div class="toast-body"><strong></strong><div class="small"></div></div>' +
      '<button type="button" class="btn-close btn-close-white m-auto me-2" data-bs-dismiss="toast"></button></div>';
    el.querySelector("strong").textContent = title || "";
    el.querySelector(".small").textContent = body || "";
    box.appendChild(el);
    var toast = new bootstrap.Toast(el, { delay: 6000 });
    el.addEventListener("hidden.bs.toast", function () { el.remove(); });
    toast.show();
  };

  // --- Thème clair / sombre -------------------------------------------------------
  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var html = document.documentElement;
      var next = html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      html.setAttribute("data-bs-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      APP.post("/profile/theme", { theme: next }).catch(function () {});
      document.dispatchEvent(new CustomEvent("themechange"));
    });
  }

  // --- Confirmation des actions irréversibles --------------------------------------
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.hasAttribute("data-confirm")) {
      var msg = form.getAttribute("data-confirm") || t.confirm;
      if (!window.confirm(msg)) { e.preventDefault(); }
    }
  }, true);

  // --- Insertion de variables {nom} dans la zone de texte --------------------------
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-insert-var]");
    if (!btn) return;
    var editor = btn.closest(".sms-editor");
    var ta = editor && editor.querySelector("textarea");
    if (!ta) return;
    var token = "{" + btn.dataset.insertVar + "}";
    var start = ta.selectionStart || ta.value.length, end = ta.selectionEnd || start;
    ta.value = ta.value.slice(0, start) + token + ta.value.slice(end);
    ta.focus();
    ta.selectionStart = ta.selectionEnd = start + token.length;
    ta.dispatchEvent(new Event("input"));
  });

  // --- Copier dans le presse-papiers -----------------------------------------------
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    var src = document.querySelector(btn.dataset.copy);
    if (src && navigator.clipboard) {
      navigator.clipboard.writeText(src.textContent.trim()).then(function () { APP.toast(t.copied, "", "success"); });
    }
  });

  // --- Soumission automatique des filtres (select) ---------------------------------
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });

  // --- Mise à jour des compteurs « data-live » -------------------------------------
  function setLive(name, value, hideZero) {
    document.querySelectorAll('[data-live="' + name + '"]').forEach(function (el) {
      el.textContent = value;
      if (hideZero) el.hidden = !value;
    });
  }
  APP.setLive = setLive;

  // --- Socket.IO : événements temps réel -------------------------------------------
  if (APP.socket && window.io) {
    var socket = io("/dashboard", { transports: ["websocket", "polling"] });
    APP.socketClient = socket;
    var dot = document.getElementById("live-status");
    socket.on("connect", function () { dot && dot.classList.add("on"); });
    socket.on("disconnect", function () { dot && dot.classList.remove("on"); });

    socket.on("inbox.new", function (m) {
      var el = document.querySelector('[data-live="unread"]');
      if (el) { var n = (parseInt(el.textContent, 10) || 0) + 1; setLive("unread", n, true); }
      if (!document.body.classList.contains("page-inbox")) {
        APP.toast((t.newSms || "Nouveau SMS") + " " + (m.contact || m.phone), m.body, "primary");
      }
      document.dispatchEvent(new CustomEvent("app:inbox", { detail: m }));
    });
    socket.on("notification", function (n) {
      var el = document.querySelector('[data-live="notifications"]');
      if (el) setLive("notifications", (parseInt(el.textContent, 10) || 0) + 1, true);
      if (n.type === "device.offline" || n.type === "abuse.detected" || n.type === "message.failed") {
        APP.toast(n.title, n.body, "danger");
      } else if (n.type === "campaign.completed") {
        APP.toast(n.title, n.body, "success");
      }
    });
    ["device.update", "device.paired", "message.new", "message.update", "campaign.update"].forEach(function (ev) {
      socket.on(ev, function (data) { document.dispatchEvent(new CustomEvent("app:" + ev, { detail: data })); });
    });

    // Statuts en direct dans les tableaux (data-message-id)
    document.addEventListener("app:message.update", function (e) {
      var m = e.detail;
      document.querySelectorAll('[data-message-id="' + m.id + '"] .status-cell').forEach(function (cell) {
        fetch("/messages/" + m.id + "/badge").then(function (r) { return r.text(); }).then(function (html) { cell.innerHTML = html; });
      });
    });
    // Appareils : batterie / état
    document.addEventListener("app:device.update", function (e) {
      var d = e.detail;
      document.querySelectorAll('[data-device-id="' + d.id + '"]').forEach(function (row) {
        var st = row.querySelector("[data-field=status]");
        if (st) {
          st.innerHTML = '<span class="badge text-bg-' + (d.status === "online" ? "success" : "danger") +
            ' status-badge">' + (d.status === "online" ? (t.deviceOnline || "online") : (t.deviceOffline || "offline")) + "</span>";
        }
        var bat = row.querySelector("[data-field=battery]");
        if (bat && d.battery_level != null) bat.textContent = d.battery_level + "%";
      });
    });
  }

  // --- Sélecteur d'appareil : limiter le choix de SIM ------------------------------
  document.querySelectorAll("[data-device-select]").forEach(function (sel) {
    var form = sel.form, sim = form && form.querySelector('select[name="sim_slot"]');
    if (!sim) return;
    var update = function () {
      var opt = sel.selectedOptions[0];
      var sims = opt && opt.dataset.sims ? JSON.parse(opt.dataset.sims) : [0, 1];
      Array.from(sim.options).forEach(function (o) {
        if (o.value === "") return;
        o.disabled = sims.length > 0 && sims.indexOf(parseInt(o.value, 10)) === -1;
      });
    };
    sel.addEventListener("change", update);
    update();
  });

  window.APP = APP;
})();
