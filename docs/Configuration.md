# Configuration

Each app is set up in the UI (see [Installation](Installation.md)) and changed later with **Reconfigure**. There is
nothing to add to `configuration.yaml`.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| Slug | | The app's part of every address: the panel is `/proxy-<slug>` and the app is served at `/api/ingress_proxy/<slug>/`. Lowercase letters, digits and underscores. Fixed once the app is added. |
| Sidebar title | | The panel's name in the sidebar and its toolbar. |
| App address | | Where Home Assistant reaches the app: `http://` or `https://`, a host, an optional port and an optional path (`http://zigbee2mqtt:8080`, `https://nas.lan:8443/app`). |
| Sidebar icon | `mdi:application` | Any Material Design icon. |
| Administrators only | on | Only Home Assistant administrators see the panel and get through the proxy. Turn off to let every Home Assistant user open the app. |
| Remember the app's login in Home Assistant | on | The app's cookies are kept in Home Assistant instead of the browser. Log in to the app once and you're logged in everywhere, on every device. See [Privacy and Security](Privacy-and-Security.md#remembered-logins) for what that means for shared logins. |

Under **Advanced**:

| Setting | Default | What it does |
| --- | --- | --- |
| Keep the /api/ingress_proxy/slug path | off | Off: the app is sent requests as if it lived at `/` (`/api/ingress_proxy/music/settings` reaches the app as `/settings`). On: the app gets the full path. Turn on only for an app you've told it lives at `/api/ingress_proxy/<slug>` (a "base URL" or "base path" setting). |
| Verify the app's SSL certificate | on | Only matters for an `https://` address. Turn off for an app with a self-signed certificate. |
| Extra request headers | none | Sent with every request to the app, one `name: value` per line. For an app that accepts an API token in a header, this logs everyone in without the app's login page. |
| Browser storage entries | none | Written to the browser's local storage before the app loads, one `key: value` per line. For an app that keeps its login there. |

The two Advanced lists are typed like YAML:

```yaml
Authorization: Bearer eyJhbGciOi...
X-Api-Key: 1234
```

## What the app is told

Every request the app gets carries:

| Header | Value |
| --- | --- |
| `X-Ingress-Path` | `/api/ingress_proxy/<slug>`: the path the browser sees the app at, the same header Home Assistant OS ingress sends |
| `X-Forwarded-Prefix` | the same path, under the more common name |
| `X-Forwarded-Host`, `X-Forwarded-Proto` | Home Assistant's address and scheme, as the browser used them |
| `X-Forwarded-For` | the browser's IP address, as Home Assistant sees it |

Apps built to work with Home Assistant OS ingress read `X-Ingress-Path` and work as they are.

## Which apps work

The proxy passes pages through as they are; it doesn't edit them. So an app works if its pages link to their own
files with relative addresses (`static/app.js`), or if it builds its links from `X-Ingress-Path`, or if it has a
base-path setting (use **Keep the /api/ingress_proxy/slug path** with it). Apps that offer Home Assistant OS ingress
meet the first two by design.

An app whose pages link to `/static/app.js` from the root, with no base-path setting, loads Home Assistant's address
instead of its own and shows a blank or broken page. See [Troubleshooting](Troubleshooting.md#the-panel-is-blank-or-unstyled).

## Examples

These are starting points: check your app's own documentation for its port and login options.

**Music Assistant** (its web page keeps its login token in the browser's storage):

| Setting | Value |
| --- | --- |
| Slug | `music` |
| App address | `http://music-assistant:8095` |
| Browser storage entries | `ma_access_token: <a long-lived token from Music Assistant>` |

**Zigbee2MQTT**:

| Setting | Value |
| --- | --- |
| Slug | `zigbee` |
| App address | `http://zigbee2mqtt:8080` |

Zigbee2MQTT's page talks to Zigbee2MQTT over a websocket at `api`, next to the page, so it works through the proxy as
it is. If Zigbee2MQTT has a frontend `auth_token`, pick one of these:

- **Let Home Assistant's login guard it** (simplest): with Zigbee2MQTT's frontend port not published to your network,
  only the proxy can reach it, so remove `auth_token` from Zigbee2MQTT's `frontend:` settings.
- **Keep the token, and have it filled in for you**: add both of these under **Browser storage entries**, using your
  Zigbee2MQTT token. They're the entries Zigbee2MQTT's own login page saves.

  ```yaml
  z2m-auth_0: true
  z2m-token_0: your-auth-token
  ```

  Without them, the page shows its own token prompt the first time, and remembers the token in that browser.

**ESPHome dashboard**:

| Setting | Value |
| --- | --- |
| Slug | `esphome` |
| App address | `http://esphome:6052` (or `http://127.0.0.1:6052` if ESPHome uses host networking) |

With the proxy in front, the ESPHome dashboard can run without a password of its own, as long as its port isn't
published to your network: your Home Assistant login guards it.
