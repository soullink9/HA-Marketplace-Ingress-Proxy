# Privacy and Security

## What leaves your Home Assistant

Nothing goes to the internet. Home Assistant connects only to the app addresses you entered. There's no analytics,
update check or third-party service.

## Which addresses an app can have

Any `http://` or `https://` address Home Assistant can reach, including private ones on your network and
`127.0.0.1` on Home Assistant's own host: showing apps that aren't published to your network is what the proxy is for.
That also means **an administrator who adds an app can reach, through it, anything Home Assistant can reach**. Only
Home Assistant administrators can add or change apps, and they can already do as much in other ways.

The one exception is cloud metadata services (`169.254.169.254`, and `fd00:ec2::254` on AWS): Home Assistant running
in a cloud VM can reach them, and they hand out the VM's credentials. The setup form refuses an address that is, or
whose name looks up to, one of them, and an app whose name later leads to one isn't started. A name's address is only
checked then, not on every request, so a name whose DNS you don't control could still be pointed at one later.

## Who can open an app

- The panel is only listed for, and the session it needs is only given to, people logged in to Home Assistant, and
  only to administrators unless you turned **Administrators only** off.
- Every request through the proxy needs that session. A session is a random token that opens one app only, lasts an
  hour, and is renewed by the panel while it's open. Each app keeps its session in a cookie of its own name, limited
  to the app's path. Changing or removing the app ends every session for it at once. Sessions live in memory, so a
  restart ends them too; open panels get a new one when they reconnect.
- A session remembers which Home Assistant user it was given to, and that user is checked again on every request:
  deleting a user, deactivating them, or taking away their administrator rights (for an administrators-only app)
  shuts them out at once. A websocket that is already open stays open until it closes.
- The app's own address is never given to the browser. If the app's port isn't published to your network, the proxy
  is the only way in.

## Proxy only apps you trust

This is the important one. The app is shown **on Home Assistant's own address**. To the browser, its pages are Home
Assistant's pages, so the app's scripts can do what Home Assistant's own scripts can: read your Home Assistant login
from the browser's storage and act as you. An app you wouldn't give your Home Assistant password to shouldn't be
proxied.

The proxy can't put a wall between the app and Home Assistant in the browser: a sandboxed iframe without access to
its own address breaks almost every app. What it does is keep the app's replies from acting on Home Assistant. It
removes response headers that would otherwise act on Home Assistant itself:

| Header | Why it's removed |
| --- | --- |
| `Strict-Transport-Security` | would force HTTPS on Home Assistant's whole address |
| `Clear-Site-Data` | an app's logout could wipe Home Assistant's own login from the browser |
| `Service-Worker-Allowed` | would let the app's service worker take over pages outside its own path |
| `X-Frame-Options`, and `frame-ancestors` in `Content-Security-Policy` | would stop the app showing in its panel |

It also:

- keeps redirects inside the app. A redirect to the app's own address or a path in it is pointed at the app's path on
  Home Assistant's address. A redirect anywhere else (another site, `//host`, another part of Home Assistant, or a
  path that climbs out with `..`) is refused with a 502, so an app can't be used to send people off to another site.
- keeps the app's cookies inside the app's path (see below), and drops cookies named `__Host-…` or `__Secure-…`,
  which would claim the whole of Home Assistant's address, and any cookie named like the proxy's own session cookies.
- refuses paths that try to climb out of the app with `..`, and never passes on the browser's `X-Forwarded-*`
  headers, so a browser can't pretend to be somewhere else.
- never sends the proxy's session cookies to the app.

## Remembered logins

With **Remember the app's login in Home Assistant** on, the app's cookies are kept in Home Assistant, in
`/config/.storage/ingress_proxy.user_cookies`, and never reach the browser. They're kept **for each Home Assistant
user separately**. That means:

- Logging in once logs you in on every device you use, and survives the browser clearing its cookies.
- Each Home Assistant user has their own login to the app. One user's login, an administrator's say, is never sent on
  another user's requests, and logging out of the app logs out only you.
- Deleting the app from Home Assistant deletes its kept cookies, and deleting a Home Assistant user deletes theirs.

Before 1.5.0 the kept cookies were shared by everyone who could open the panel. Updating deletes that shared file
(`ingress_proxy.cookies`), since nobody can tell whose login it holds, so everyone logs in to such apps once more.

With it off, the browser keeps the app's cookies, limited to the app's own path on Home Assistant's address (a cookie
for another path is moved under the app's), and every person logs in to the app separately.

## App logins

A username and password under **Login** are saved in the app's config entry, like any integration's credentials
(`/config/.storage/core.config_entries`). Home Assistant uses them only on its own requests to the app:

- They go to the app in the `Authorization` header, and nowhere else. The browser never gets them: not in a reply,
  not in the form (Reconfigure leaves the password field empty), not in the logs.
- For an ESPHome Device Builder, Home Assistant logs in once and keeps the token it gets in memory only, sending it
  instead of the password. A restart, or changing the app, makes it log in again.
- The app's login pop-up (`WWW-Authenticate`) isn't passed to the browser, since its answer would be replaced anyway.
- **Everyone who can open the panel uses that login**, unlike a remembered login, since there's only one username and
  password. Keep **Administrators only** on when the account matters.
- If the app hangs while Home Assistant logs in to it, requests waiting for that login give up after 20 seconds with
  a 502 instead of queueing.

## Headers and browser storage entries

Both are saved in the app's config entry, like any integration's credentials.

- **Extra request headers** are added by Home Assistant on the way to the app. The browser never sees them.
- **Browser storage entries** are given to the browser of everyone who opens the panel, and stay in that browser's
  local storage afterwards. Anyone who can open the panel can read them. Use a token you'd be happy for those people
  to have, and keep **Administrators only** on when it matters. Their keys are used exactly as typed, since the app
  reads them by name, but the keys Home Assistant's own frontend keeps there (`hassTokens`, its login, and
  `selectedTheme`, `selectedLanguage`, `defaultPanel` and the sidebar settings) are refused, so an entry can't
  overwrite your Home Assistant login or settings. The app's own scripts still share that storage, as above.

## Limits

So that one app, or a page that misbehaves, can't tie up Home Assistant:

- An app can have at most 100 requests and 25 websockets open through the proxy at once. Past that, the proxy answers
  `503 Service Unavailable` with `Retry-After: 5` until some finish.
- A websocket message can be at most 16 MiB, either way. A bigger one closes the websocket with code 1009 (message too
  big).
- Both ends of every websocket are pinged every 30 seconds, and a side that stops answering is closed, so a browser or
  app that vanished doesn't keep the websocket open.
- A reply from the app that sends nothing for 5 minutes is given up on. Streams, live logs and downloads aren't
  otherwise limited in time, as long as something arrives.

## The logs

The proxy logs, at debug level only, the method, app address and status of each request, and when websockets open
and close. The address is logged without its query string (shown as `?…`), since apps sometimes put tokens there.
Headers, cookies, passwords and the content of requests and messages are never logged. A login the app refuses is
logged as a warning with the app's title and the reason the app gave, never the password.
