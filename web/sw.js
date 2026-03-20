// Service Worker - 全国動物園スタンプラリー
const CACHE_NAME = 'zoo-v1';

// キャッシュするアプリシェル（バージョンはアクセス時に自動更新）
const APP_SHELL = [
  '/',
  '/styles.css',
  '/styles.layout.css',
  '/app.js',
  '/icon.svg',
  '/manifest.json',
];

// インストール: アプリシェルをキャッシュ
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(c => c.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

// アクティベート: 古いキャッシュを削除
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// フェッチ戦略
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API・認証・アップロードは常にネットワーク
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/auth/') ||
    url.pathname.startsWith('/login') ||
    url.pathname.startsWith('/logout') ||
    url.pathname.startsWith('/uploads/')
  ) {
    return; // ブラウザデフォルト（ネットワーク）に委ねる
  }

  // 外部リソース（Leaflet CDN など）はネットワーク優先、フォールバックなし
  if (url.origin !== location.origin) return;

  // 同一オリジンの静的リソース: キャッシュ優先 → ネットワーク取得 & キャッシュ更新
  e.respondWith(
    caches.match(e.request).then(cached => {
      const fetchPromise = fetch(e.request).then(res => {
        if (res && res.ok) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => null);

      return cached || fetchPromise;
    })
  );
});
