/* ==========================================================================
   GMSA UTAS — Prayer times widget
   Times are entered by an admin (Admin > Content > Prayer Times) and
   server-rendered directly into the page — this script only computes the
   "next prayer" countdown from what's already in the DOM. No external API
   or geolocation involved.

   Usage: include this script on any page that has elements with the
   following attributes:
     data-prayer-name="Fajr" + data-prayer-time="HH:MM"  -> on the
                                   Fajr/Dhuhr/Asr/Maghrib/Isha cells (not
                                   Sunrise — not used for the countdown); the
                                   cell's own text is already the
                                   human-readable time, rendered server-side
     data-prayer-next-name   -> filled with the name of the next prayer
     data-prayer-next-time   -> filled with the time of the next prayer
     data-prayer-countdown   -> filled with a live "HH:MM:SS" countdown
     data-prayer-date        -> filled with today's date
   ========================================================================== */

(function () {
  'use strict';

  var PRAYER_ORDER_FOR_COUNTDOWN = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha'];

  var timeElements = document.querySelectorAll('[data-prayer-time]');
  var hasCountdownTargets = document.querySelector(
    '[data-prayer-next-name], [data-prayer-next-time], [data-prayer-countdown]'
  );

  var dateEl = document.querySelector('[data-prayer-date]');
  if (dateEl) {
    dateEl.textContent = new Date().toLocaleDateString(undefined, {
      weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
    });
  }

  if (!timeElements.length || !hasCountdownTargets) {
    return; // no prayer times set, or no countdown widget on this page
  }

  function formatTime(hours, minutes) {
    var suffix = hours >= 12 ? 'PM' : 'AM';
    var displayHour = hours % 12 === 0 ? 12 : hours % 12;
    return displayHour + ':' + String(minutes).padStart(2, '0') + ' ' + suffix;
  }

  var timings = {};
  timeElements.forEach(function (el) {
    var key = el.getAttribute('data-prayer-name');
    var raw = el.getAttribute('data-prayer-time');
    if (key && raw) timings[key] = raw;
  });

  var nextNameEl = document.querySelector('[data-prayer-next-name]');
  var nextTimeEl = document.querySelector('[data-prayer-next-time]');
  var countdownEl = document.querySelector('[data-prayer-countdown]');

  var now = new Date();
  var todaysTimes = PRAYER_ORDER_FOR_COUNTDOWN
    .filter(function (name) { return timings[name]; })
    .map(function (name) {
      var parts = timings[name].split(':').map(Number);
      var d = new Date();
      d.setHours(parts[0], parts[1], 0, 0);
      return { name: name, date: d };
    });

  if (!todaysTimes.length) return;

  var next = todaysTimes.find(function (p) { return p.date > now; });
  if (!next) {
    // All of today's prayers have passed — next is tomorrow's first prayer.
    next = { name: todaysTimes[0].name, date: new Date(todaysTimes[0].date.getTime() + 24 * 60 * 60 * 1000) };
  }

  if (nextNameEl) nextNameEl.textContent = next.name;
  if (nextTimeEl) nextTimeEl.textContent = formatTime(next.date.getHours(), next.date.getMinutes());

  if (countdownEl) {
    var tick = function () {
      var diff = next.date - new Date();
      if (diff <= 0) {
        countdownEl.textContent = '00:00:00';
        clearInterval(interval);
        return;
      }
      var h = Math.floor(diff / 3600000);
      var m = Math.floor((diff % 3600000) / 60000);
      var s = Math.floor((diff % 60000) / 1000);
      countdownEl.textContent = [h, m, s].map(function (n) {
        return String(n).padStart(2, '0');
      }).join(':');
    };
    tick();
    var interval = setInterval(tick, 1000);
  }
})();
