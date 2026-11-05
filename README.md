# Disclosure

This project was primarily created for personal use and for my family. While I may provide updates, they are not guaranteed. However, if you've stumbled upon this and find it interesting, feel free to download and use it. I had a great time building this and hope you enjoy it.

Note: Built with the assistance of Claude.ai. I’m putting it here now so you don't have to hunt for it below.

<p align="center">
  <img src="docs/images/logo.png" alt="soullink9 logo" width="200">
</p>

# Ingress Proxy for Home Assistant

> Open the web pages of the apps running next to Home Assistant (ESPHome, Music Assistant, Zigbee2MQTT and the like)
> as sidebar panels, behind your Home Assistant login, without opening their ports to your network.

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=soullink9&repository=HA-Marketplace-Ingress-Proxy&category=integration)

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ingress_proxy)

Home Assistant OS has "ingress" for its apps (once called add-ons): the app's page opens inside Home Assistant, and
only people logged in to Home Assistant can reach it. If you run Home Assistant in Docker, your apps are separate
containers and that feature isn't there. This integration brings it back. Home Assistant fetches each app's page itself and serves it at
its own address, so your browser never talks to the app directly.

## What you get

For every app you add:

- **A sidebar panel** with the title and icon you choose, showing the app's page.
- **Your Home Assistant login is the only login.** Only people logged in to Home Assistant get through, and by default
  only administrators. The app's own port doesn't need to be published at all.
- **Everything the app does works:** pages, uploads, downloads, live updates over websockets, server-sent events.
- **Optional: stay logged in to the app itself.** For apps with their own login, Home Assistant can remember that login
  so you log in once and stay logged in on every device. Apps that keep their login in the browser's storage can have
  it written there for them.

## Requirements

- Home Assistant 2026.9 or newer
- Apps that Home Assistant can reach over HTTP or HTTPS, for example containers on the same Docker network

## Install

Install **Ingress Proxy** from the Marketplace (**Settings → Marketplace**, Home Assistant 2026.11 and newer), or copy the [`custom_components/ingress_proxy`](custom_components/ingress_proxy) folder into `/config/custom_components/`, then restart Home Assistant.

Then go to **Settings → Devices & services → Add integration → Ingress Proxy** once for each app. Full steps are in
the [Installation](docs/Installation.md) page.

## Documentation

| Page | What's in it |
| --- | --- |
| [Installation](docs/Installation.md) | Installing, adding an app, changing or removing one |
| [Configuration](docs/Configuration.md) | Every setting, with examples for common apps |
| [Privacy and Security](docs/Privacy-and-Security.md) | Who can open what, where logins are kept, what to proxy and what not to |
| [Troubleshooting](docs/Troubleshooting.md) | Blank panels, "no valid session", apps that won't load or log in |
| [Development](docs/Development.md) | How it works inside, for maintainers |

## Privacy

The integration only talks to the app addresses you enter. There is no cloud service and no telemetry. Headers and
browser storage values you enter (API tokens, say) are kept in the app's config entry, like any integration
credential. Read [Privacy and Security](docs/Privacy-and-Security.md) before proxying an app: it runs on Home Assistant's
own address, so only proxy apps you trust.

## Contributing

Bug reports and pull requests are welcome, although replies are not guaranteed (see the [Disclosure](#disclosure) above). [CONTRIBUTING.md](CONTRIBUTING.md) explains the code layout and how to run the tests.

## Credits

- **[Home Assistant developer documentation](https://developers.home-assistant.io/)**. The integration follows its
  documented patterns (config flow, custom panels, the websocket API, `runtime_data`).
- **[aiohttp](https://docs.aiohttp.org/)**, the web library Home Assistant runs on, which does the HTTP and websocket
  work.
- **[Claude](https://claude.ai)** by Anthropic, which assisted in building this project.

No code was copied or adapted from other projects. The icon is original artwork made for this project.

## Disclaimer

This is an unofficial community project. It is not affiliated with or endorsed by the Open Home Foundation or the
makers of any app you proxy with it.

## License

[GNU General Public License v3.0](LICENSE) (GPL-3.0).
