// The sidebar panel for one proxied app: a slim toolbar, and the app in an iframe served by the proxy.
//
// Before the iframe loads, the panel asks Home Assistant for a session (the ingress_proxy/session websocket
// command) and stores it as a cookie limited to the app's path, which the browser then sends with every request
// the iframe makes. The session is renewed while the panel is open, and again whenever Home Assistant's websocket
// reconnects (sessions are kept in memory, so a restart ends them).
//
// Home Assistant loads this file as a module with ?v=<integration version> on the end. The version is part of the
// element's name too: see async_setup_entry in __init__.py.

const VERSION = new URL(import.meta.url).searchParams.get("v") || "0";
const ELEMENT_NAME = `ingress-proxy-panel-${VERSION.replaceAll(".", "-")}`;

// Sessions last an hour on the server; renewing every 20 minutes leaves room for a missed renewal or two.
const RENEW_EVERY_MS = 20 * 60 * 1000;

// Material Design Icons paths: menu and home.
const MENU_ICON = "M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z";
const HOME_ICON = "M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z";

const STYLE = `
  :host {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--primary-background-color);
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    height: var(--header-height, 56px);
    padding: 0 12px;
    flex: none;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, var(--text-primary-color));
  }
  .toolbar button {
    display: flex;
    padding: 8px;
    border: 0;
    border-radius: 50%;
    background: none;
    color: inherit;
    cursor: pointer;
  }
  .toolbar button:hover {
    background: rgba(127, 127, 127, 0.2);
  }
  .toolbar button[hidden] {
    display: none;
  }
  .title {
    font-size: 20px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .message {
    margin: 24px auto;
    max-width: 560px;
    padding: 16px;
    color: var(--primary-text-color);
  }
  .message button {
    margin-top: 12px;
    padding: 8px 16px;
    border: 0;
    border-radius: 4px;
    background: var(--primary-color);
    color: var(--text-primary-color);
    cursor: pointer;
  }
  iframe {
    flex: 1;
    display: block;
    width: 100%;
    border: 0;
  }
`;

class IngressProxyPanel extends HTMLElement {
  // Home Assistant sets these three properties: the hass object (often), the panel's settings, and whether the
  // screen is narrow (when the sidebar is hidden and the menu button is needed to open it).
  set hass(hass) {
    this._hass = hass;
    this._start();
  }

  set panel(panel) {
    this._panel = panel;
    this._start();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuButton) {
      this._menuButton.hidden = !narrow;
    }
  }

  connectedCallback() {
    if (this._started) {
      this._listen();
    }
  }

  disconnectedCallback() {
    this._stopListening();
  }

  // Build the panel once Home Assistant has given it both the hass object and its settings.
  _start() {
    if (this._started || !this._hass || !this._panel) {
      return;
    }
    this._started = true;
    this._slug = this._panel.config.slug;
    this._render();
    this._listen();
    this._open();
  }

  _render() {
    const root = this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLE;

    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";
    this._menuButton = this._iconButton(MENU_ICON, "Open the Home Assistant menu", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })),
    );
    this._menuButton.hidden = !this._narrow;
    const homeButton = this._iconButton(HOME_ICON, "Back to Home Assistant", () => {
      history.pushState(null, "", "/");
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    });
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = this._panel.title || "";
    toolbar.append(this._menuButton, homeButton, title);

    this._message = document.createElement("div");
    this._message.className = "message";
    this._message.hidden = true;

    root.append(style, toolbar, this._message);
  }

  _iconButton(path, label, onClick) {
    const button = document.createElement("button");
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24"><path fill="currentColor" d="${path}"/></svg>`;
    button.addEventListener("click", onClick);
    return button;
  }

  // Get a session, then show the app. If there's no session to be had, say why instead of showing an empty frame.
  async _open() {
    try {
      await this._renew();
    } catch (err) {
      this._showError(err);
      return;
    }
    this._message.hidden = true;
    if (!this._frame) {
      this._frame = document.createElement("iframe");
      this._frame.title = this._panel.title || "";
      this._frame.allow = "fullscreen";
      this._frame.src = `${this._appPath}/`;
      this.shadowRoot.append(this._frame);
    }
  }

  async _renew() {
    const answer = await this._hass.callWS({ type: "ingress_proxy/session", slug: this._slug });
    if (!this._storageWritten) {
      // The app runs on Home Assistant's address, so this is the same browser storage the app reads: an app that
      // keeps its login there finds it already in place.
      for (const [key, value] of Object.entries(answer.local_storage || {})) {
        try {
          localStorage.setItem(key, value);
        } catch (err) {
          console.warn("Ingress Proxy: could not write browser storage", key, err);
        }
      }
      this._storageWritten = true;
    }
    // An earlier Ingress Proxy kept the session in one cookie for every app, at /api/ingress_proxy. A browser that
    // still has it sends it alongside this app's own; delete it so it can't get in the way.
    document.cookie = `${answer.cookie}=; Path=/api/ingress_proxy; Max-Age=0`;
    const secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      `${answer.cookie}=${answer.session}; Path=${answer.path}; Max-Age=${answer.max_age}; SameSite=Strict${secure}`;
    this._appPath = answer.path;
    this._renewedAt = Date.now();
  }

  // Renew quietly; if it fails, the current session usually has time left, and the next renewal tries again.
  async _renewQuietly() {
    try {
      await this._renew();
    } catch (err) {
      console.warn("Ingress Proxy: could not renew the session", err);
    }
  }

  _showError(err) {
    const reason = (err && err.message) || String(err);
    this._message.replaceChildren();
    const text = document.createElement("div");
    text.textContent = `This app can't be opened: ${reason}`;
    const retry = document.createElement("button");
    retry.textContent = "Try again";
    retry.addEventListener("click", () => this._open());
    this._message.append(text, retry);
    this._message.hidden = false;
  }

  // While the panel is on screen: renew on a timer, when the tab comes back after a long time in the background
  // (browsers slow timers down there), and when Home Assistant's websocket reconnects after a restart.
  _listen() {
    if (this._timer) {
      return;
    }
    this._timer = setInterval(() => this._renewQuietly(), RENEW_EVERY_MS);
    this._onVisible = () => {
      if (document.visibilityState === "visible" && Date.now() - (this._renewedAt || 0) > RENEW_EVERY_MS / 2) {
        this._renewQuietly();
      }
    };
    document.addEventListener("visibilitychange", this._onVisible);
    this._onReconnect = () => {
      if (this._frame) {
        this._renewQuietly();
      } else {
        this._open(); // the first try failed, perhaps because Home Assistant was restarting
      }
    };
    this._connection = this._hass.connection;
    this._connection.addEventListener("ready", this._onReconnect);
  }

  _stopListening() {
    clearInterval(this._timer);
    this._timer = null;
    if (this._onVisible) {
      document.removeEventListener("visibilitychange", this._onVisible);
    }
    if (this._connection && this._onReconnect) {
      this._connection.removeEventListener("ready", this._onReconnect);
    }
  }
}

if (!customElements.get(ELEMENT_NAME)) {
  customElements.define(ELEMENT_NAME, IngressProxyPanel);
}
