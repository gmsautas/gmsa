/* ==========================================================================
   GMSA UTAS — shared site behaviours
   Loaded on every page. Handles: active nav highlighting, footer year,
   toast notifications, mobile menu helpers, and small UI utilities.
   ========================================================================== */

(function () {
  'use strict';

  /* ---- Footer year ----------------------------------------------------- */
  document.querySelectorAll('[data-current-year]').forEach(function (el) {
    el.textContent = new Date().getFullYear();
  });

  /* ---- Active nav link highlighting ------------------------------------- */
  var current = (window.location.pathname.split('/').pop() || 'index.html');
  document.querySelectorAll('[data-nav-link]').forEach(function (link) {
    var target = link.getAttribute('data-nav-link');
    if (target === current) {
      link.classList.add('active');
      link.setAttribute('aria-current', 'page');
    }
  });

  /* ---- Lucide icons ------------------------------------------------------ */
  if (window.lucide && typeof window.lucide.createIcons === 'function') {
    window.lucide.createIcons();
  }

  /* ---- Toast notifications ------------------------------------------------ */
  function ensureToastRegion() {
    var region = document.getElementById('toast-region');
    if (!region) {
      region = document.createElement('div');
      region.id = 'toast-region';
      region.setAttribute('role', 'status');
      region.setAttribute('aria-live', 'polite');
      document.body.appendChild(region);
    }
    return region;
  }

  window.showToast = function (message, duration) {
    var region = ensureToastRegion();
    var toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    region.appendChild(toast);
    setTimeout(function () {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity .25s ease';
      setTimeout(function () { toast.remove(); }, 250);
    }, duration || 3200);
  };

  /* ---- Animated counters (for stat tiles with [data-counter]) -------------- */
  function animateCounter(el) {
    var target = parseFloat(el.getAttribute('data-counter'));
    if (isNaN(target)) return;
    var prefix = el.getAttribute('data-prefix') || '';
    var suffix = el.getAttribute('data-suffix') || '';
    var decimals = parseInt(el.getAttribute('data-decimals') || '0', 10);
    var duration = 1200;
    var start = null;

    function step(timestamp) {
      if (!start) start = timestamp;
      var progress = Math.min((timestamp - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var value = target * eased;
      el.textContent = prefix + value.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
      }) + suffix;
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  var counterObserver = ('IntersectionObserver' in window)
    ? new IntersectionObserver(function (entries, observer) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            animateCounter(entry.target);
            observer.unobserve(entry.target);
          }
        });
      }, { threshold: 0.4 })
    : null;

  document.querySelectorAll('[data-counter]').forEach(function (el) {
    if (counterObserver) {
      counterObserver.observe(el);
    } else {
      animateCounter(el);
    }
  });

  /* ---- Reveal-on-scroll animation for [data-animate] elements -------------- */
  var revealObserver = ('IntersectionObserver' in window)
    ? new IntersectionObserver(function (entries, observer) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('animate-fade-up');
            observer.unobserve(entry.target);
          }
        });
      }, { threshold: 0.15 })
    : null;

  document.querySelectorAll('[data-animate]').forEach(function (el) {
    if (revealObserver) {
      el.style.opacity = '0';
      revealObserver.observe(el);
      revealObserver._show = function () { el.style.opacity = ''; };
    }
  });
  // Ensure elements become visible once animated (opacity reset via class)
  document.querySelectorAll('[data-animate]').forEach(function (el) {
    el.addEventListener('animationstart', function () {
      el.style.opacity = '1';
    });
  });

  /* ---- Generic helper: format currency (GHS / configurable) ----------------- */
  window.formatCurrency = function (amount, currency) {
    currency = currency || 'GHS';
    return new Intl.NumberFormat('en-GH', {
      style: 'currency',
      currency: currency,
      maximumFractionDigits: 2
    }).format(amount);
  };

  /* ---- Generic helper: format date -------------------------------------------- */
  window.formatDate = function (isoString, opts) {
    var date = new Date(isoString);
    return date.toLocaleDateString(undefined, opts || { year: 'numeric', month: 'short', day: 'numeric' });
  };

  /* ---- Client-side table pagination (Alpine component) ------------------------
     Usage: wrap a .table-card in x-data="tablePager(<row count>, <page size>)",
     add x-show="page === Math.floor(<row index>/pageSize)" to each <tr>, and
     include partials/table_pager_foot.html for the Prev/Next footer. Registered
     via alpine:init so it works regardless of whether Alpine's deferred script
     or this plain script tag finishes loading first. */
  document.addEventListener('alpine:init', function () {
    window.Alpine.data('tablePager', function (total, pageSize) {
      return {
        page: 0,
        pageSize: pageSize || 10,
        total: total || 0,
        get pageCount() { return Math.max(1, Math.ceil(this.total / this.pageSize)); },
        get rangeStart() { return this.total === 0 ? 0 : this.page * this.pageSize + 1; },
        get rangeEnd() { return Math.min((this.page + 1) * this.pageSize, this.total); },
        prev: function () { if (this.page > 0) this.page--; },
        next: function () { if (this.page < this.pageCount - 1) this.page++; },
        goto: function (p) { this.page = p; },
      };
    });
  });
})();
