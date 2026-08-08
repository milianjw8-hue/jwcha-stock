/* 스윙콘솔 서비스워커 — 앱 껍데기는 캐시, 스캔 결과는 네트워크 우선 */
// 껍데기는 캐시 우선이라, app.js/app.css/guide.html 을 고치면 반드시 올려야 한다
const CACHE = "jwcha-stock-v6";
const SHELL = ["./", "./index.html", "./guide.html", "./app.css", "./app.js",
  "./manifest.webmanifest", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.pathname.endsWith(".json")) {
    // 결과 파일: 네트워크 우선, 실패 시 마지막 캐시
    e.respondWith(
      fetch(e.request).then(r => {
        const cp = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, cp));
        return r;
      }).catch(() => caches.match(e.request))
    );
  } else {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});
