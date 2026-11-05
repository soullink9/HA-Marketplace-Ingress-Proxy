# Privacy and Security

## What leaves your Home Assistant

Nothing goes to the internet. Home Assistant connects only to the app addresses you entered. There's no analytics,
update check or third-party service.

## Who can open an app

- The panel is only listed for, and the session it needs is only given to, people logged in to Home Assistant, and
  only to administrators unless you turned **Administrators only** off.
- Every request through the proxy needs that session. A session is a random token that opens one app only, lasts an
  hour, and is renewed by the panel while it's open. Changing or removing the app ends every session for it at once.
  Sessions live in memory, so a restart ends them too; open panels get a new one when they reconnect.
- The app's own address is never given to the browser. If the app's port isn't published to your network, the proxy
  is the only way in.

## Proxy only apps you trust

This is the important one. The app is shown **on Home Assistant's own address**. To the browser, its pages are Home
Assistant's pages, so the app's scripts can do what Home Assistant's own scripts can: read your Home Assistant login
from the browser's storage and act as you. An app you wouldn't give your Home Assistant password to shouldn't be
proxied.

For the same reason the proxy removes response headers that would otherwise act on Home Assistant itself:

| Header | Why it's removed |
| --- | --- |
| `Strict-Transport-Security` | would force HTTPS on Home Assistant's whole address |
| `Clear-Site-Data` | an app's logout could wipe Home Assistant's own login from the browser |
| `Service-Worker-Allowed` | would let the app's service worker take over pages outside its own path |
| `X-Frame-Options`, and `frame-ancestors` in `Content-Security-Policy` | would stop the app showing in its panel |

The proxy also refuses paths that try to climb out of the app with `..`, and never passes on the browser's
`X-Forwarded-*` headers, so a browser can't pretend to be somewhere else.

## Remembered logins

With **Remember the app's login in Home Assistant** on, the app's cookies are kept in Home Assistant, in
`/config/.storage/ingress_proxy.cookies`, and never reach the browser. That means:

- Logging in once logs in every device, and survives the browser clearing its cookies.
- **Everyone who can open the panel shares that login.** If a non-administrator can open the app, they act as whoever
  logged in to it. Keep this on for apps only you use, or for apps whose own login doesn't matter behind Home
  Assistant's.
- Logging out of the app logs out everyone.
- Deleting the app from Home Assistant deletes its kept cookies.

With it off, the browser keeps the app's cookies, limited to the app's own path on Home Assistant's address, and every
person logs in to the app separately.

## Headers and browser storage entries

Both are saved in the app's config entry, like any integration's credentials.

- **Extra request headers** are added by Home Assistant on the way to the app. The browser never sees them.
- **Browser storage entries** are given to the browser of everyone who opens the panel, and stay in that browser's
  local storage afterwards. Anyone who can open the panel can read them. Use a token you'd be happy for those people
  to have, and keep **Administrators only** on when it matters.

## The logs

The proxy logs, at debug level only, the method, app address and status of each request, and when websockets open
and close. The address includes any query string, so check debug logs before sharing them if an app puts tokens
there. Headers, cookies and the content of requests and messages are never logged.
