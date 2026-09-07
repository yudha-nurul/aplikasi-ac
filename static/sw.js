self.addEventListener('install', (e) => {
  console.log('[Service Worker] Installed');
});

self.addEventListener('fetch', (e) => {
  // Mengizinkan aplikasi mengambil request secara normal
  e.respondWith(fetch(e.request));
});