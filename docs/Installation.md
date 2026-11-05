# Installation

## From the Marketplace

In Home Assistant 2026.11 and newer, open **Settings → Marketplace**, search for **Ingress Proxy** and install it, then
restart Home Assistant. Updates then show up under **Settings → Updates**.

If it isn't listed yet, add it from its link instead: this repository's address,
`https://github.com/soullink9/HA-Marketplace-Ingress-Proxy`. Adding from a link needs Home Assistant to be connected to GitHub.

To install by hand instead, follow the steps below.

## 1. Copy the integration

Copy [`custom_components/ingress_proxy`](../custom_components/ingress_proxy) from this repository into your
Home Assistant configuration folder, so you end up with `/config/custom_components/ingress_proxy/manifest.json`.
Restart Home Assistant.

## 2. Make sure Home Assistant can reach the app

Home Assistant fetches the app's pages itself, so the address you enter is the one **Home Assistant** uses, not your
browser. With Docker, the simplest setup is to put the app on the same Docker network as Home Assistant and use its
container name:

```yaml
# docker-compose.yml (excerpt)
services:
  homeassistant:
    networks: [home]
  music-assistant:
    networks: [home]
    # no "ports:" needed for the web page: only Home Assistant talks to it
networks:
  home:
```

Here Home Assistant reaches Music Assistant at `http://music-assistant:8095`. If Home Assistant uses host networking,
use `http://127.0.0.1:<port>` or the machine's address instead, and publish the port to the host only
(`127.0.0.1:8095:8095`) so it isn't open to your network.

To check, open a terminal in the Home Assistant container and run `curl -I http://music-assistant:8095/`. Any reply,
even an error, means the address works.

## 3. Add the app

1. Go to **Settings → Devices & services → Add integration → Ingress Proxy**.
2. Fill in the form:
   - **Slug**: a short name of lowercase letters, digits and underscores, such as `music`. The panel's address will be
     `/proxy-music`. It can't be changed later.
   - **Sidebar title** and **Sidebar icon**: how the panel appears in the sidebar.
   - **App address**: from step 2.
   - **Administrators only** and **Remember the app's login in Home Assistant**: see [Configuration](Configuration.md).
   - **Advanced**: only needed for some apps; see [Configuration](Configuration.md).
3. Select **Submit**. Home Assistant checks it can reach the app before saving.

The panel appears in the sidebar straight away. Repeat for each app.

## Changing an app

**Settings → Devices & services → Ingress Proxy**, then the app's **⋮ → Reconfigure**. Everything but the slug can be
changed. The panel updates when you save, and anyone with the panel open gets a fresh session.

## Removing an app

**Settings → Devices & services → Ingress Proxy**, then the app's **⋮ → Delete**. The panel goes away, and any login
Home Assistant was keeping for the app is deleted.

## Updating

Replace the `custom_components/ingress_proxy` folder with the new one and restart Home Assistant. Browsers pick up the
new panel by themselves.

## Uninstalling

Delete every app as above, then delete `/config/custom_components/ingress_proxy` and restart.
