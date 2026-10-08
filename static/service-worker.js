// Caches a handful of frequently-visited pages (the bottom-nav tabs + the plan
// week-detail view) so switching between them feels instant instead of a full
// reload every time. Everything else - assets, auth pages, Strava OAuth, any
// POST - passes straight through untouched, same as before.
//
// Strategy: stale-while-revalidate for the cacheable pages (serve the cached
// copy immediately, quietly re-fetch in the background and update the cache
// for next time). After any successful POST the cache is emptied so your own
// actions (mark done, link Strava, etc.) are never left showing a stale page,
// and the four tab pages are immediately re-fetched in the background - so the
// next tap on a tab is both fresh AND instant, instead of a cold network fetch.

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

// Only the canonical bottom-nav tab URLs are re-fetched eagerly after a save;
// anything else that was cached (week-detail pages, ?week=&day= variants of
// Today) is just dropped, so one tap doesn't trigger a burst of requests.
function isTabPage(pathname) {
  return pathname === "/athlete" || pathname === "/strava/overview" || /^\/plan\/\d+(\/calendar)?$/.test(pathname);
}

async function rebuildPageCache() {
  const cache = await caches.open(PAGE_CACHE);
  const keys = await cache.keys();
  const today = new Date().toISOString().slice(0, 10);

  // Drop everything first, so nothing from before the change can be served...
  await Promise.all(keys.map((key) => cache.delete(key)));

  // ...then re-fetch the tab pages so the next tap on one is fresh and instant.
  await Promise.all(
    keys.map(async (key) => {
      const url = new URL(key.url);
      if (!isTabPage(url.pathname)) return;
      if (url.pathname === "/athlete") {
        if (url.searchParams.get("_cachedate") !== today) return; // an old day's entry
        url.searchParams.delete("_cachedate");
      }
      if (url.search) return;
      try {
        const response = await fetch(url.toString());
        // A redirect means we were bounced (e.g. to the login page) - never
        // cache that under a tab's key.
        if (response.ok && !response.redirected) await cache.put(key, response);
      } catch (e) {
        // Offline or the server hiccuped: the page just gets fetched normally next visit.
      }
    })
  );
}

// Several saves in a row (ticking off a few workouts) shouldn't each start a
// full rebuild - if one is running, ask for exactly one more pass afterwards.
let rebuildRunning = false;
let rebuildPending = false;
async function refreshPageCache() {
  if (rebuildRunning) {
    rebuildPending = true;
    return;
  }
  rebuildRunning = true;
  try {
    do {
      rebuildPending = false;
      await rebuildPageCache();
    } while (rebuildPending);
  } finally {
    rebuildRunning = false;
  }
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
    // Any mutation: let it through, then rebuild the page cache so the next
    // navigation can't reuse anything from before this change. A 4xx/5xx means
    // nothing changed, so the cache is still good. (A form POST's manual
    // redirect comes back as status 0, which counts as success.)
    event.respondWith(
      fetch(req).then((res) => {
        if (res.status < 400) {
          try {
            event.waitUntil(refreshPageCache());
          } catch (e) {
            refreshPageCache();
          }
        }
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
