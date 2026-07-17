/* ==========================================================================
   Live-updating election results (admin/election_results.html).
   Opens a WebSocket to /admin/elections/{id}/results/ws, which pushes a
   fresh tally snapshot every few seconds, and patches the already-rendered
   page in place -- no polling fetch, no full-page refresh.
   ========================================================================== */

(function () {
  'use strict';

  var root = document.getElementById('results-root');
  if (!root) return; // no positions configured yet -- nothing to keep live

  var electionId = root.getAttribute('data-election-id');
  var isClosed = root.getAttribute('data-closed') === '1';
  var indicator = document.getElementById('live-indicator');

  function setIndicator(state, label, iconName) {
    if (!indicator) return;
    indicator.setAttribute('data-state', state);
    indicator.innerHTML = '<i data-lucide="' + iconName + '" class="w-3 h-3' +
      (state === 'connecting' ? ' animate-spin' : '') + '"></i> ' + label;
    if (window.lucide) window.lucide.createIcons();
  }

  function pluralize(n, noun) {
    return n + ' ' + (n === 1 ? noun : noun + 's');
  }

  function updateTurnout(payload) {
    var votersEl = document.getElementById('stat-voters-count');
    var votedEl = document.getElementById('stat-voted-count');
    var turnoutEl = document.getElementById('stat-turnout-percent');
    var fillEl = document.getElementById('turnout-progress-fill');
    if (votersEl) votersEl.textContent = payload.voters_count;
    if (votedEl) votedEl.textContent = payload.voted_count;
    if (turnoutEl) turnoutEl.textContent = payload.turnout_percent + '%';
    if (fillEl) fillEl.style.width = payload.turnout_percent + '%';
  }

  function updateUncontested(card, p) {
    var candEl = card.querySelector('[data-candidate-id="' + p.candidate_id + '"]');
    if (!candEl) return;

    var yesCount = card.querySelector('[data-yes-count]');
    var noCount = card.querySelector('[data-no-count]');
    var yesFill = card.querySelector('[data-yes-fill]');
    var noFill = card.querySelector('[data-no-fill]');
    var badgeSlot = candEl.querySelector('[data-result-badge]');

    if (yesCount) yesCount.textContent = pluralize(p.yes_votes, 'vote');
    if (noCount) noCount.textContent = pluralize(p.no_votes, 'vote');
    var yesPct = p.total_votes ? (p.yes_votes / p.total_votes * 100) : 0;
    var noPct = p.total_votes ? (p.no_votes / p.total_votes * 100) : 0;
    if (yesFill) yesFill.style.width = yesPct + '%';
    if (noFill) noFill.style.width = noPct + '%';

    if (badgeSlot) {
      var html = '';
      if (p.total_votes > 0) {
        if (p.yes_votes > p.no_votes) {
          html = '<span class="badge badge-green">' + (isClosed ? 'Elected' : 'Passing') + '</span>';
        } else if (p.no_votes > p.yes_votes) {
          html = '<span class="badge badge-red">' + (isClosed ? 'Rejected' : 'Failing') + '</span>';
        } else {
          html = '<span class="badge badge-gray">Tied</span>';
        }
      }
      badgeSlot.innerHTML = html;
    }
  }

  function updateContested(card, p) {
    var totalEl = card.querySelector('[data-total-votes]');
    if (totalEl) totalEl.textContent = pluralize(p.total_votes, 'vote') + ' cast for this position.';

    var list = card.querySelector('[data-candidates-list]');
    if (!list) return;

    var sorted = p.candidates.slice().sort(function (a, b) { return b.votes - a.votes; });
    var topVotes = sorted.length ? sorted[0].votes : 0;
    var tiedAtTop = sorted.filter(function (c) { return c.votes === topVotes; }).length > 1;
    var label = isClosed ? 'Winner' : 'Leading';

    sorted.forEach(function (c) {
      var el = list.querySelector('[data-candidate-id="' + c.candidate_id + '"]');
      if (!el) return;

      var vp = el.querySelector('[data-votes-percent]');
      if (vp) vp.textContent = c.votes + ' (' + c.percent + '%)';

      var fill = el.querySelector('[data-fill]');
      if (fill) fill.style.width = c.percent + '%';

      var badgeSlot = el.querySelector('[data-lead-badge]');
      if (badgeSlot) {
        badgeSlot.innerHTML = (c.votes > 0 && c.votes === topVotes)
          ? '<span class="badge badge-green">' + label + (tiedAtTop ? ' (tied)' : '') + '</span>'
          : '';
      }

      // Move into rank order -- appendChild relocates an existing node
      // rather than cloning it, so this reorders in place.
      list.appendChild(el);
    });
  }

  function applyPayload(payload) {
    updateTurnout(payload);
    payload.positions.forEach(function (p) {
      var card = root.querySelector('[data-position-id="' + p.position_id + '"]');
      if (!card) return;
      if (p.contested) {
        updateContested(card, p);
      } else {
        updateUncontested(card, p);
      }
    });
  }

  var socket = null;
  var retryDelay = 2000;
  var intentionallyClosed = false;

  function connect() {
    var scheme = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    socket = new WebSocket(scheme + window.location.host + '/admin/elections/' + electionId + '/results/ws');

    setIndicator('connecting', 'Connecting…', 'loader-circle');

    socket.addEventListener('open', function () {
      retryDelay = 2000;
      setIndicator('live', 'Live', 'radio');
    });

    socket.addEventListener('message', function (event) {
      try {
        applyPayload(JSON.parse(event.data));
      } catch (e) {
        // Malformed frame -- ignore, next tick will self-correct.
      }
    });

    socket.addEventListener('close', function () {
      if (intentionallyClosed) return;
      setIndicator('offline', 'Reconnecting…', 'wifi-off');
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.5, 15000);
    });

    socket.addEventListener('error', function () {
      socket.close();
    });
  }

  window.addEventListener('beforeunload', function () {
    intentionallyClosed = true;
    if (socket) socket.close();
  });

  connect();
})();
