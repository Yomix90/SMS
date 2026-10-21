/* Compteur SMS : même logique que app/services/sms_encoding.py (GSM-7 / UCS-2, segments). */
(function (global) {
  "use strict";
  var BASIC = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?" +
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";
  var EXT = "^{}\\[~]|€\f";
  var basic = new Set(BASIC.split("")), ext = new Set(EXT.split(""));

  function analyze(text) {
    text = text || "";
    var chars = Array.from(text);          // points de code (emoji = 1)
    var gsm = chars.every(function (c) { return basic.has(c) || ext.has(c); });
    var units = chars.map(function (c) {
      if (gsm) return ext.has(c) ? 2 : 1;
      return c.codePointAt(0) > 0xFFFF ? 2 : 1;
    });
    var total = units.reduce(function (a, b) { return a + b; }, 0);
    var single = gsm ? 160 : 70, multi = gsm ? 153 : 67;
    var segments, per, remaining;
    if (total === 0) { segments = 0; per = single; remaining = single; }
    else if (total <= single) { segments = 1; per = single; remaining = single - total; }
    else {
      segments = 1; var used = 0;
      units.forEach(function (u) { if (used + u > multi) { segments++; used = 0; } used += u; });
      per = multi; remaining = multi - used;
    }
    return { encoding: gsm ? "gsm7" : "ucs2", characters: chars.length, units: total, segments: segments,
             perSegment: per, remaining: remaining };
  }

  function render(el, info) {
    var t = (global.APP && global.APP.i18n) || {};
    el.innerHTML =
      '<span><span class="seg">' + info.segments + "</span> " + (t.segments || "SMS") + "</span>" +
      "<span>" + info.characters + " " + (t.chars || "caractères") + "</span>" +
      "<span>" + info.remaining + " " + (t.remaining || "restants") + "</span>" +
      '<span class="' + (info.encoding === "ucs2" ? "warn" : "") + '">' +
      (info.encoding === "ucs2" ? (t.unicode || "Unicode") : (t.gsm || "GSM")) + "</span>";
  }

  function bind(textarea) {
    var target = document.querySelector(textarea.dataset.smsCounter);
    if (!target) return;
    var update = function () { render(target, analyze(textarea.value)); };
    textarea.addEventListener("input", update);
    update();
  }

  global.SmsCounter = { analyze: analyze, bind: bind };
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-sms-counter]").forEach(bind);
  });
})(window);
