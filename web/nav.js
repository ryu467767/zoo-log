(function () {
  var menuBtn = document.getElementById('menuBtn');
  var drawer = document.getElementById('drawer');
  var overlay = document.getElementById('drawerOverlay');
  var closeBtn = document.getElementById('drawerClose');

  function openDrawer() {
    if (!drawer) return;
    drawer.classList.add('is-open');
    if (overlay) overlay.classList.add('is-open');
    drawer.removeAttribute('aria-hidden');
  }
  function closeDrawer() {
    if (!drawer) return;
    drawer.classList.remove('is-open');
    if (overlay) overlay.classList.remove('is-open');
    drawer.setAttribute('aria-hidden', 'true');
  }

  if (menuBtn) menuBtn.addEventListener('click', openDrawer);
  if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
  if (overlay) overlay.addEventListener('click', closeDrawer);

  // 現在ページのリンクをアクティブに
  var path = window.location.pathname.replace(/\/$/, '') || '/';
  document.querySelectorAll('.drawer-link[href]').forEach(function (link) {
    var href = link.getAttribute('href').replace(/\/$/, '') || '/';
    if (href === path) link.classList.add('drawer-link--active');
  });

  // ログイン状態取得
  fetch('/api/me', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (me) {
      var loggedIn = !!(me && me.user_id);
      var loginStatus = document.getElementById('loginStatus');
      var googleLogin = document.getElementById('googleLogin');
      var logoutBtn = document.getElementById('logoutBtn');
      var galleryBtn = document.getElementById('galleryBtn');

      if (loginStatus) loginStatus.textContent = loggedIn ? (me.email || 'ログイン中') : '';
      if (googleLogin) googleLogin.style.display = loggedIn ? 'none' : '';
      if (logoutBtn) logoutBtn.style.display = loggedIn ? '' : 'none';
      if (galleryBtn) galleryBtn.style.display = loggedIn ? '' : 'none';

      if (googleLogin) googleLogin.addEventListener('click', function () { location.href = '/login'; });
      if (logoutBtn) logoutBtn.addEventListener('click', function () {
        fetch('/logout', { method: 'POST', credentials: 'same-origin' })
          .then(function () { location.href = '/'; });
      });
    })
    .catch(function () {});

  // ===== お知らせベル（ヘッダーの更新情報ポップアップ）=====
  // 最新の日付は HTML に書かれた1件目の .bell-popover__date を読む。
  // 前回見た日付より新しければ、バッジを出してベルを揺らす。
  var bellBtn = document.getElementById('updateBellBtn');
  var bellBadge = document.getElementById('bellBadge');
  var bellPop = document.getElementById('bellPopover');
  if (bellBtn && bellBadge && bellPop) {
    var bellDateEl = bellPop.querySelector('.bell-popover__date');
    var bellLatest = bellDateEl ? bellDateEl.textContent.trim() : '';
    // 水族館とは別のキーにする（同じブラウザで両方見ても混ざらないように）
    var BELL_SEEN_KEY = 'zoo_lastSeenUpdate';
    var bellLastSeen = '';
    try { bellLastSeen = localStorage.getItem(BELL_SEEN_KEY) || ''; } catch (e) {}

    if (bellLatest && bellLatest > bellLastSeen) {
      bellBadge.hidden = false;
      bellBtn.classList.add('is-shaking');
    }

    var closeBellPopover = function () {
      bellPop.hidden = true;
      bellBtn.setAttribute('aria-expanded', 'false');
    };
    var openBellPopover = function () {
      bellPop.hidden = false;
      bellBtn.setAttribute('aria-expanded', 'true');
      bellBadge.hidden = true;
      bellBtn.classList.remove('is-shaking');
      try { if (bellLatest) localStorage.setItem(BELL_SEEN_KEY, bellLatest); } catch (e) {}
    };

    bellBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (bellPop.hidden) openBellPopover(); else closeBellPopover();
    });
    document.addEventListener('click', function (e) {
      if (!bellPop.hidden && !bellPop.contains(e.target) && e.target !== bellBtn) closeBellPopover();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeBellPopover();
    });
  }
})();
