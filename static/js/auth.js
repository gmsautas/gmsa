/* ==========================================================================
   GMSA UTAS — auth guards
   Load after api.js on every protected page.

   Member pages:   var user = await requireMember();
   Admin pages:    var user = await requireAdmin();
   Logout button:  onclick="authLogout()"
   ========================================================================== */

(function () {
  'use strict';

  function memberLoginUrl() {
    var p = window.location.pathname;
    if (p.includes('/member/')) return '../login.html';
    return 'login.html';
  }

  function adminLoginUrl() {
    var p = window.location.pathname;
    if (p.includes('/admin/')) return 'login.html';
    return 'admin/login.html';
  }

  window.requireMember = async function () {
    if (!window.api.isLoggedIn()) {
      window.location.href = memberLoginUrl();
      return null;
    }
    var res = await window.api.get('/auth/me');
    if (!res || !res.ok) {
      window.location.href = memberLoginUrl();
      return null;
    }
    var user = await res.json();
    var avatarEl = document.getElementById('user-avatar');
    var nameEl   = document.getElementById('user-name');
    if (avatarEl) avatarEl.textContent = user.initials;
    if (nameEl)   nameEl.textContent   = user.name;
    return user;
  };

  window.requireAdmin = async function () {
    if (!window.api.isLoggedIn()) {
      window.location.href = adminLoginUrl();
      return null;
    }
    var res = await window.api.get('/auth/me');
    if (!res || !res.ok) {
      window.location.href = adminLoginUrl();
      return null;
    }
    var user = await res.json();
    if (user.role !== 'admin' && user.role !== 'superadmin') {
      window.location.href = adminLoginUrl();
      return null;
    }
    var avatarEl = document.getElementById('admin-avatar');
    var nameEl   = document.getElementById('admin-name');
    var titleEl  = document.getElementById('admin-title');
    if (avatarEl) avatarEl.textContent = user.initials;
    if (nameEl)   nameEl.textContent   = user.name;
    if (titleEl)  titleEl.textContent  = user.role === 'superadmin' ? 'Super Admin' : 'Admin';
    return user;
  };

  window.authLogout = function () {
    window.api.logout();
    var p = window.location.pathname;
    if (p.includes('/admin/')) {
      window.location.href = 'login.html';
    } else {
      window.location.href = '../login.html';
    }
  };
})();
