# Changelog

## 1.5.0 (2026-11-22)

Security hardening, from an outside security review. **Read before updating:** remembered app logins are now kept
for each Home Assistant user, so everyone logs in to such apps once more after updating.

- **Remembered logins are per user.** With **Remember the app's login in Home Assistant** on, each Home Assistant user
  now has their own login to the app: one user's login (an administrator's, say) is never sent on another user's
  requests, and logging out of the app logs out only you. The shared logins kept by earlier versions are deleted when
  Home Assistant starts, since nobody can tell whose they are. Deleting a Home Assistant user deletes theirs.
- **Changes to a Home Assistant user apply at once.** The proxy checks the user behind a panel session on every
  request, so a user who is deleted, deactivated, or no longer an administrator (for an administrators-only app) is
  shut out straight away instead of when their session runs out.
- **Redirects stay inside the app.** A redirect from the app to another site, to another part of Home Assistant, or
  climbing out with `..`, is refused with a 502 instead of being followed by the browser.
- **The app's cookies stay inside the app.** A cookie's path is always kept under the app's own path (also with **Keep
  the /api/ingress_proxy/slug path** on), and cookies named `__Host-…` or `__Secure-…` are dropped.
- **Each app has its own session cookie** (`ingress_proxy_session_<slug>`), and no session cookie ever reaches an app.
- **Home Assistant's own browser storage keys can't be set.** Browser storage entries named like Home Assistant's
  login (`hassTokens`) or settings are refused by the form, and never written by the panel.
- **Cloud metadata services can't be proxied.** An app address that is, or looks up to, `169.254.169.254` or
  `fd00:ec2::254` is refused. Every other address, including private ones on your network, still works.
- **Limits**, so one app or page can't tie up Home Assistant: at most 100 requests and 25 websockets open per app (503
  past that), websocket messages up to 16 MiB (was unlimited), websockets pinged every 30 seconds so dead ones close,
  and a reply that sends nothing for 5 minutes is given up on.
- Requests waiting for Home Assistant to log in to an ESPHome Device Builder give up after 20 seconds instead of
  queueing behind an app that hangs.
- Debug logs no longer include query strings, where apps sometimes put tokens.
- [Privacy and Security](docs/Privacy-and-Security.md) describes all of the above, and which addresses an app can have.

### Repository

- GitHub's workflows pin every action, including the Marketplace's checks and `hassfest`, to a commit, and check out
  without keeping the token. The GitHub release workflow has no permissions by default; only its release job may
  publish.

## 1.4.1 (2026-11-15)

- Fixed: updating to 1.4.0 from the Marketplace failed with "The installed content has no usable manifest.json where
  Home Assistant looks for it". 1.4.0 told the Marketplace to install the release's zip, which it unpacks one folder
  too deep. The Marketplace now installs the integration from the release's tag again, as 1.3.0 did. Skip 1.4.0 and
  update straight to 1.4.1.

## 1.4.0 (2026-11-15)

**Needs Home Assistant 2026.11 or newer.** Only the latest Home Assistant release is supported from now on; the
Marketplace keeps offering 1.3.0 to older installations.

- Made for the Home Assistant 2026.11 Marketplace: it installs the release's checked `ingress_proxy.zip` (this broke
  installing from the Marketplace; fixed in 1.4.1), and a first install needs no restart, going straight to adding
  your first app.
- In an app's **Login** settings, **Password** and **How to log in** now appear only once a username is typed.
- "Saved" after Reconfigure now uses Home Assistant's own wording.

### Repository

- Built for Home Assistant 2026.11 only, with nothing kept for older releases: Python 3.14 syntax throughout (no
  `from __future__ import annotations`, enforced by ruff's pyupgrade rules), probatio instead of voluptuous, a typed
  `HassKey` for the shared data, and the form's conditional fields.
- Every check runs on Python 3.14, and `test_release.py` checks `hacs.json` against the README and the built zip.
- Installation, update and removal steps describe the Marketplace.

## 1.3.0 (2026-11-15)

- New: a **Login** section in the app's settings. Give an app's username and password and Home Assistant logs in to
  it itself, so the app never asks for them in the browser, and the password never reaches the browser. **HTTP
  Basic** suits most apps and the older ESPHome dashboard; **ESPHome Device Builder** logs in to the dashboard of newer
  ESPHome releases and sends the token it gets on every request and websocket. Saving checks the login, and
  Reconfigure keeps the saved password unless a new one is typed.

### Repository

- Gitea is the main copy: its runner checks every push and publishes releases (`.gitea/workflows/`), with nothing
  downloaded from GitHub, so it keeps working when GitHub can't be reached. Gitea push-mirrors to GitHub, and every
  push from it publishes on GitHub the releases GitHub doesn't have yet, with the same files and notes, where the
  Marketplace installs from.
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
