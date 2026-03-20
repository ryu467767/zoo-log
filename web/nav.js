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
})();
