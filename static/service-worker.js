// Deliberately minimal: no offline caching, since training data and Strava
// sync need to always be live. This just satisfies the "has an active service
// worker" requirement browsers use to decide an app is installable.
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
