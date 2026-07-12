/* ==========================================================================
   GMSA UTAS — API client
   Thin fetch wrapper with JWT auth (access + refresh tokens), auto-retry
   on 401, and redirect to login on session expiry.

   Usage:
     var res = await api.get('/events');
     var data = await res.json();

   Tokens are stored in localStorage as 'gmsa_access' / 'gmsa_refresh'.
   Set window.API_BASE before loading this file to override the default.
   ========================================================================== */

(function () {
  'use strict';

  var BASE = (window.API_BASE || 'http://localhost:8000') + '/api';

  function getAccess()  { return localStorage.getItem('gmsa_access'); }
  function getRefresh() { return localStorage.getItem('gmsa_refresh'); }

  function saveTokens(access, refresh) {
    localStorage.setItem('gmsa_access', access);
    if (refresh) localStorage.setItem('gmsa_refresh', refresh);
  }

  function clearTokens() {
    localStorage.removeItem('gmsa_access');
    localStorage.removeItem('gmsa_refresh');
  }

  function loginUrl() {
    var p = window.location.pathname;
    if (p.includes('/admin/')) return 'login.html';
    if (p.includes('/member/')) return '../login.html';
    return 'login.html';
  }

  async function _request(method, path, body, _retried) {
    var headers = { 'Content-Type': 'application/json' };
    var access = getAccess();
    if (access) headers['Authorization'] = 'Bearer ' + access;

    var opts = { method: method, headers: headers };
    if (body !== undefined) opts.body = JSON.stringify(body);

    var res;
    try {
      res = await fetch(BASE + path, opts);
    } catch (err) {
      if (window.showToast) window.showToast('Network error — is the server running?');
      throw err;
    }

    if (res.status === 401 && !_retried) {
      var rt = getRefresh();
      if (rt) {
        var rRes;
        try {
          rRes = await fetch(BASE + '/auth/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: rt })
          });
        } catch (_) { /* network failure */ }
        if (rRes && rRes.ok) {
          var tokens = await rRes.json();
          saveTokens(tokens.access_token, tokens.refresh_token);
          return _request(method, path, body, true);
        }
      }
      clearTokens();
      window.location.href = loginUrl();
      return res;
    }

    return res;
  }

  window.api = {
    get:    function (path)       { return _request('GET',    path, undefined); },
    post:   function (path, body) { return _request('POST',   path, body); },
    patch:  function (path, body) { return _request('PATCH',  path, body); },
    put:    function (path, body) { return _request('PUT',    path, body); },
    delete: function (path)       { return _request('DELETE', path, undefined); },

    isLoggedIn: function () { return !!getAccess(); },

    login: async function (email, password) {
      var res = await fetch(BASE + '/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email, password: password })
      });
      if (res.ok) {
        var data = await res.json();
        saveTokens(data.access_token, data.refresh_token);
      }
      return res;
    },

    logout: function () { clearTokens(); },

    register: function (payload) {
      return fetch(BASE + '/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    }
  };
})();
