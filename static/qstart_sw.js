// Qstart Service Worker
const CACHE = 'qstart-v1';
const ASSETS = ['/static/qstart_icon.svg'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(ks => Promise.all(
      ks.filter(k => k !== CACHE).map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // APIは常にネットワーク優先(古いデータを見せない)
  if (url.pathname.includes('/api/')) return;
  // 静的ファイルだけキャッシュ
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
        return res;
      }).catch(() => r))
    );
  }
});
