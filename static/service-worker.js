// Caches a handful of frequently-visited pages (the bottom-nav tabs + the plan
// week-detail view) so switching between them feels instant instead of a full
// reload every time. Everything else - assets, auth pages, Strava OAuth, any
// POST - passes straight through untouched, same as before.
//
// Strategy: stale-while-revalidate for the cacheable pages (serve the cached
// copy immediately, quietly re-fetch in the background and update the cache
// for next time), plus a hard wipe of the whole page cache on any POST. That
// guarantees your own actions (mark done, link Strava, etc.) are never left
// showing a stale cached page - the very next navigation after one always
// does a normal fresh fetch, then caching resumes from there.

const PAGE_CACHE = "page-cache-v1";

function isCacheablePage(pathname) {
  if (pathname === "/athlete") return true;
  if (pathname === "/strava/overview") return true;
  if (/^\/plan\/\d+(\/(calendar|week\/\d+))?$/.test(pathname)) return true;
  return false;
}

// The Today page's content (which day is "today", what's highlighted and
// selected by default) depends on the real calendar date, not just the URL -
// caching it plainly would show yesterday's "today" first thing after
// midnight, until the background refresh quietly fixed it. Keying its cache
// entry to today's date instead makes a stale entry a clean cache miss once
// the date rolls over, rather than something that has to be remembered and
// explicitly invalidated.
function cacheKeyFor(request) {
  const url = new URL(request.url);
  if (url.pathname === "/athlete") {
    url.searchParams.set("_cachedate", new Date().toISOString().slice(0, 10));
  }
  return url.toString();
}

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names.filter((name) => name.startsWith("page-cache-") && name !== PAGE_CACHE).map((name) => caches.delete(name))
        )
      )
      .then(() => self.clients.claim())
  );
});

async function staleWhileRevalidate(event) {
  const request = event.request;
  const cache = await caches.open(PAGE_CACHE);
  const cacheKey = cacheKeyFor(request);
  const cached = await cache.match(cacheKey);

  const networkFetch = fetch(request)
    .then((response) => {
      if (response && response.ok) {
        cache.put(cacheKey, response.clone());
      }
      return response;
    })
    .catch(() => null);

  if (cached) {
    // Keep the service worker alive until this finishes even though the
    // response below doesn't wait on it - otherwise the browser can kill it
    // mid-fetch once respondWith's promise resolves, and the cache never
    // gets refreshed for next time.
    event.waitUntil(networkFetch);
    return cached;
  }

  // Nothing cached yet for this page - behave like a normal page load.
  const fresh = await networkFetch;
  return fresh || Response.error();
}

self.addEventListener("fetch", (event) => {
  const req = event.request;

  if (req.method !== "GET") {
    // Any mutation: let it through, then drop the whole page cache so the
    // next navigation - wherever it lands - fetches fresh instead of
    // reusing anything cached from before this change.
    event.respondWith(
      fetch(req).then((res) => {
        caches.delete(PAGE_CACHE);
        return res;
      })
    );
    return;
  }

  const url = new URL(req.url);
  if (req.mode === "navigate" && url.origin === self.location.origin && isCacheablePage(url.pathname)) {
    event.respondWith(staleWhileRevalidate(event));
    return;
  }

  event.respondWith(fetch(req));
});
