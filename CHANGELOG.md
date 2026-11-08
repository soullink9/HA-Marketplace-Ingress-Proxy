# Changelog

## Unreleased

### Repository

- Gitea is the main copy: its runner checks every push and publishes releases (`.gitea/workflows/`), with nothing
  downloaded from GitHub, so it keeps working when GitHub can't be reached. Gitea push-mirrors to GitHub, and a
  version tag arriving there publishes the same release on GitHub, where the Marketplace installs from.
- The release helpers live in `scripts/` and are used by both: the version check and notes, building the zip, and
  publishing on Gitea. Every release's notes now start with how to install it.
- The release's zip is now always `ingress_proxy.zip` (it had the version in its name), so
  `.../releases/latest/download/ingress_proxy.zip` always gets the newest. Each zip is checked before it's published.

## 1.2.0 (2026-11-04)

- Moved to its own repository, [HA-Marketplace-Ingress-Proxy](https://github.com/soullink9/HA-Marketplace-Ingress-Proxy), so it can be installed and updated
  from the Home Assistant Marketplace (Home Assistant 2026.11 and newer). Installing by hand works as before.
- Lists its categories, so it shows up in the right place when browsing the Marketplace. No other changes to how
  the integration works.

## 1.1.1 (2026-11-02)

- Fixed: an app could refuse to open with "no valid session" in a browser or app web view (such as the Lena
  wall display) that still held the session cookie from an older Ingress Proxy. The proxy now checks every value
  of the session cookie the browser sends, and the panel deletes the old cookie.

## 1.1.0 (2026-11-01)

Initial release.

- Show the web page of an app on your network as a Home Assistant sidebar panel, served from Home Assistant's own
  address so the app's port needn't be open to your network.
- Only people logged in to Home Assistant get through; administrators only unless you choose otherwise. Sessions open
  one app, are renewed while the panel is open, and end when the app is changed or removed.
- Set up in the UI, one entry per app, with Reconfigure to change everything but the slug. Home Assistant checks it can
  reach the app before saving.
- HTTP and websockets: uploads and replies are streamed, compressed replies pass through untouched, websocket
  subprotocols and close codes are kept both ways, and a reply the app breaks off is cut off for the browser too.
- The app's login can be kept in Home Assistant (log in once, on every device), or left to each browser under the
  app's own path.
- Extra request headers (an API token, say) and browser storage entries for apps that keep their login there.
- Redirects back into the app are rewritten; headers that would act on Home Assistant itself (HSTS, Clear-Site-Data,
  Service-Worker-Allowed) and anything that would stop the app showing in its panel are removed.
- The panel shows why an app can't be opened, renews its session after a Home Assistant restart, and only shows its
  menu button when the sidebar is hidden.
- Brand icons, and tests with and without aiohttp.
