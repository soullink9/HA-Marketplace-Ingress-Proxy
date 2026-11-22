# Troubleshooting

## Turn on debug logging

Add this to `configuration.yaml` and restart, or use **Enable debug logging** on the integration's page:

```yaml
logger:
  logs:
    custom_components.ingress_proxy: debug
```

The log then shows every request with the app's reply status, refused requests, and websockets opening and closing.

## "Home Assistant can't reach the app at that address"

Home Assistant, not your browser, has to reach the address. Check from inside the Home Assistant container:
`curl -I http://music-assistant:8095/`. Usual causes: the containers aren't on the same Docker network, the port is
the app's published port instead of its internal one, or the app isn't running. See
[Installation](Installation.md#2-make-sure-home-assistant-can-reach-the-app).

## "The app's SSL certificate can't be verified"

The app uses HTTPS with a self-signed certificate. Turn off **Verify the app's SSL certificate** under **Advanced**,
or use the app's plain `http://` address if it has one inside your Docker network.

## "The app refused the username or password"

Saving checked the login under **Login** and the app said no. Check the username and password (they're case
sensitive), and **How to log in**: an ESPHome Device Builder needs **ESPHome Device Builder**, most other apps
**HTTP Basic**. An ESPHome Device Builder also refuses for a while after several wrong passwords.

## "No ESPHome Device Builder answered at that address"

**How to log in** is **ESPHome Device Builder**, but the app at that address isn't one: it's the older dashboard
built into ESPHome (choose **HTTP Basic**), or the address points at something else.

## "Home Assistant couldn't log in to ..." (502)

The app worked when it was saved but refuses the login now, usually because its password changed. Enter the new one
with **Reconfigure**. The log has a warning with the reason the app gave.

## The app still asks for its login

- Check a username is set under **Login**; Reconfigure leaves the password empty, which keeps the saved one.
- An ESPHome Device Builder showing its login screen means **How to log in** is **HTTP Basic**: choose **ESPHome
  Device Builder**.
- Some apps only take a login on their own form and ignore HTTP Basic. Keep **Remember the app's login in Home
  Assistant** on and log in once in the panel instead.

## "Ingress Proxy: no valid session"

The page was opened without its panel, or its session ran out:

- Open the app from its sidebar panel, not from a bookmark of `/api/ingress_proxy/...`.
- After a Home Assistant restart, the panel gets a new session when it reconnects. If the app still shows this, reload
  the page.
- A user who isn't an administrator gets this for an app with **Administrators only** on (they don't see its panel
  either). So does a user who was deactivated, deleted, or lost their administrator rights while the page was open:
  the proxy checks the user on every request.

## "This app can't be opened" in the panel

The panel couldn't get a session. The message says why: usually the app was deleted or changed while the page was
open (reload the page), or you aren't an administrator and the app is for administrators only. **Try again** asks
again.

## "Ingress Proxy: ... can't be reached right now" (502)

Home Assistant couldn't connect to the app: it's stopped, restarting, or its address changed. Check the app, then
**Reconfigure** if the address changed.

## "Ingress Proxy: ... tried to send the browser outside the app" (502)

The app answered with a redirect that leads out of its own path on Home Assistant's address: to another site, or to
another part of Home Assistant. The proxy refuses those (see
[Privacy and Security](Privacy-and-Security.md#proxy-only-apps-you-trust)). Usually the app doesn't know where it
lives: give it a base path (see the next section), or check it isn't redirecting to its own public address. An app
whose login goes through another site (an OAuth sign-in page) can't be proxied this way.

## "Ingress Proxy: this app has too many connections open" (503)

The app has 100 requests or 25 websockets open through the proxy at once, usually because pages of it are open in
many tabs or on many screens, or the page keeps opening websockets without closing them. Close some, and it works
again within seconds.

## The panel is blank or unstyled

The app's page loaded but its scripts and styles didn't. Open the browser's developer tools (Network tab) and look
for requests going to `/static/...` or `/assets/...` on Home Assistant's address instead of
`/api/ingress_proxy/<slug>/...`. That app links to its files from the root of the address, which only works when an
app has the whole address to itself. Options, in order:

1. Look for a base path, base URL or "behind a reverse proxy" setting in the app. Set it to
   `/api/ingress_proxy/<slug>` and turn on **Keep the /api/ingress_proxy/slug path**.
2. Check whether the app supports Home Assistant OS ingress; those apps read the `X-Ingress-Path` header the proxy
   sends.
3. Otherwise the app can't be proxied this way.

## The app keeps asking me to log in

- With **Remember the app's login in Home Assistant** on, log in once from the panel; Home Assistant keeps the login.
  If it still forgets, the app may tie its login to something else, such as its own address. Try turning the setting
  off so the browser keeps the cookies instead.
- For an app that keeps its login in the browser's storage, add its token under **Browser storage entries**.
- For an app that takes an API token in a header, add it under **Extra request headers**.

## Home Assistant says ingress_proxy can't be set up from YAML

Apps are added in the UI only. Remove the `ingress_proxy:` section from `configuration.yaml`, restart, and add each
app under **Settings → Devices & services**. Use the same slugs as before to keep the panels' addresses, and logins
Home Assistant was remembering for those apps carry over.

## Zigbee2MQTT keeps reconnecting or asks for a token

Zigbee2MQTT closes its websocket with code 4401 when its frontend `auth_token` is set and the page didn't send it; the
page then shows its token prompt. Enter the token there, or set it up once for everyone as described under
Zigbee2MQTT in [Configuration](Configuration.md#examples). If the page instead keeps reconnecting without ever asking,
check the debug log for the websocket's close code.

## Live updates stop or the app says it's disconnected

The app's websocket goes through the proxy too. Check the debug log for "Websocket closed" lines with close codes:
`1001` is the browser leaving the page, `1009` a message bigger than the proxy's 16 MiB limit, other codes come from
the app. If Home Assistant restarted, the app should reconnect on its own once the panel has its new session; reload
the panel if it doesn't.
