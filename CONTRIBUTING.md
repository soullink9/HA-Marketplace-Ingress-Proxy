# Contributing / maintaining

Thanks for helping out. This file is the map for anyone taking over the code. The standards every change keeps are below; [Development](docs/Development.md) explains how the proxy works.

## Project layout

```
custom_components/ingress_proxy/
├── __init__.py       Start-up (proxy, panel script, websocket command, kept cookies) and per-app panels
├── const.py          DOMAIN, setting names, paths, the session cookie, timeouts
├── models.py         ProxiedApp (one app's settings) and IngressProxyData (what all apps share)
├── config_flow.py    The form that adds an app, and Reconfigure; tidies and checks what's typed
├── websocket.py      ingress_proxy/session: gives the panel a session for one app
├── sessions.py       The session tokens: made, checked, expired, ended
├── proxy.py          The proxy view: HTTP streaming and the websocket relay
├── rewrite.py        What is passed on and how it's rewritten, both ways (no aiohttp, easy to test)
├── cookies.py        Set-Cookie parsing, the browser rewrite, and the kept-cookie jar
├── client.py         The proxy's own HTTP client sessions
├── frontend/panel.js The sidebar panel: toolbar, session handling, the app's iframe
├── manifest.json     Integration metadata (bump "version" on every release)
├── translations/     UI strings for the form (en.json)
└── brand/            Icons (icon.png 256 px, icon@2x.png 512 px), drawn from docs/images/icon.svg
tests/
├── README.md            What each test checks
├── stubs.py             Stand-ins for Home Assistant (and aiohttp and voluptuous for test_integration.py)
├── test_integration.py  Everything that needs only Python: rules, cookies, sessions, form, set-up, the view's decisions
├── test_live_proxy.py   Real HTTP and websockets through the real view, with a fake app (needs aiohttp)
└── test_release.py      The release scripts, publishing against a fake Gitea
scripts/                 The release helpers both release workflows use (see Releasing)
.gitea/workflows/        Tests and releases on Gitea, the main copy (no GitHub needed)
.github/workflows/       GitHub's checks (hassfest, the Marketplace's) and its copy of each release
```

## Where to change what

| To change | Look at |
| --- | --- |
| Which headers are passed on or dropped, redirects, the security policy | `rewrite.py` |
| How the app's cookies are kept or rewritten | `cookies.py` |
| Who may open an app, how long sessions last | `websocket.py`, `sessions.py`, `const.py` (`SESSION_TTL_SECONDS`) and `RENEW_EVERY_MS` in `panel.js` |
| A new setting | `const.py`, `models.py` (`ProxiedApp`), `config_flow.py`, `translations/en.json`, [Configuration](docs/Configuration.md) |
| The toolbar or the error message in the panel | `frontend/panel.js` |
| The icon | `docs/images/icon.svg`, then render it to `brand/icon.png` (256×256) and `brand/icon@2x.png` (512×512) with a transparent background |

## Conventions

- **Settings are saved flat** in the config entry's data, under the names in `const.py`. The form's Advanced section
  is only how they're shown. `ProxiedApp.from_entry_data` fills in defaults, so a new setting needs no migration.
- **Slugs never change.** They're the panel's address, the session cookie's path and the key of the kept cookies.
- **Check before anything reaches the app.** Keep the session check at the top of `_proxy`.
- **Rules go in `rewrite.py` or `cookies.py`, not in the view**, so they stay testable without aiohttp.
- **Depends on nothing outside Home Assistant.** `requirements` in the manifest stays empty.
- Keep comments and docstrings current; the code is meant to be readable by someone new.

## Checking a change

```
pip install ruff
ruff check && ruff format --check
for f in custom_components/ingress_proxy/frontend/*.js; do node --check "$f"; done
python3 tests/test_integration.py
python3 tests/test_release.py
python3 scripts/build_package.py
```

These need Python 3.13 or newer (Home Assistant 2026.9 itself runs on 3.14, which is what the workflows use), and
Node 20 or newer to check the panel script parses. Gitea's runner runs all of these on every push
(`.gitea/workflows/test.yml`); on GitHub the same checks run plus Home Assistant's `hassfest` and the Marketplace's
own checks, which come from GitHub and can only run there. `test_release.py` checks the release scripts (any change
to `scripts/` or the release workflows), and `build_package.py` builds `dist/ingress_proxy.zip` and checks it
installs. [tests/README.md](tests/README.md) lists what each test checks.

`test_integration.py` loads the real integration against stand-ins and
checks every rewrite rule, the cookie handling, sessions, the setup form, adding and removing apps, the session
command, and what the proxy view lets through, sends and returns.

```
pip install aiohttp
python3 tests/test_live_proxy.py
```

Needs aiohttp, the web library Home Assistant runs on, so neither Gitea nor GitHub runs it. It starts a fake app and Home Assistant's web server with the
real proxy view on 127.0.0.1, and acts as a browser: redirects, kept and passed-on cookies, a 1 MiB upload, gzip
passing through, streaming, an app breaking off mid-reply, security headers, and websockets with subprotocols,
binary messages and close codes in both directions. Run it after any change to `proxy.py` or `client.py`.

Neither test runs a real Home Assistant or a real browser. Try changes on a real installation too, with an app or two
you use, before releasing.

## Standards for every change

- **Readable over clever.** Write code a newcomer can follow on the first read. Prefer a few plain lines with a named
  variable to a dense one-liner, a nested conditional expression, or a trick that needs a comment to decode.
- **Comment the why.** Every function and class has a docstring. Comments explain why the code does something
  non-obvious (a quirk of the device or service, a Home Assistant rule, a deliberate trade-off), not what each line
  does.
- **Keep names consistent.** Follow the names already used in the integration: the same words for
  the same things, Home Assistant's conventions for entities and config entries, `snake_case` for Python names and
  `camelCase` for JavaScript.
- **Keep the docs in step.** When you change a function, class or behaviour, update its docstring, its comments and the
  pages under `docs/` (and the README) that describe it in the same commit.
- **Keep the tests green.** Every change keeps existing behaviour and passes all of the tests. When you
  fix a bug, add the situation that caused it to the tests so it stays fixed.
- **No new dependencies without approval.** Don't add a third-party library (to a `manifest.json`, a
  `requirements.txt` or CI) without asking in an issue first. This page says what it already depends on and why.

### Test layout

The tests use these names:

```
tests/
  stubs.py                stand-ins for Home Assistant and the libraries the integration uses
  test_integration.py     the real integration against the stand-ins and a fake device or service
  test_<subject>.py       any other checks (test_engine.py, test_blueprint.py, test_live_proxy.py)
  test_<subject>_soak.py  a long simulation: it takes a length as an argument, and CI runs a short one
  fake_<service>.py       a fake device or service, when it's too big to live inside the test
  requirements.txt        what the tests need beyond Python, if they need anything
```

Every `test_*.py` is a plain script, not a pytest suite: run it with `python3` from the repository root, and it
exits non-zero if any check fails.

### The Marketplace

`hacs.json` at the root is the Marketplace's file: the name it lists and the oldest Home Assistant version the
integration works with. Keep that version in step with the README's requirements.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/): a type, an optional scope, then a short summary in
the imperative.

```
feat: add a diagnostics download
fix: keep the last value when the service is briefly unreachable
refactor: split the coordinator's update into steps
docs: explain the safety settings
test: cover a reply with a missing field
chore: update the CI actions
```

## Releasing

Gitea is the main copy of this repository. It push-mirrors to GitHub, which is the public copy: the Marketplace
installs from GitHub's releases, so every link in the docs and release notes points there.

1. Bump `version` in `custom_components/ingress_proxy/manifest.json`. Versions go up together: there is only this
   one (the panel script takes its version from the manifest, through the `?v=` Home Assistant loads it with).
2. Add the release to the top of [CHANGELOG.md](CHANGELOG.md), under a heading `## X.Y.Z (date)` (move what's under
   `## Unreleased` into it).
3. Run the checks above, `test_live_proxy.py` included.
4. Commit and push to Gitea, then publish with Gitea's **Release** workflow (`.gitea/workflows/release.yml`),
   either way:
   - **Actions → Release → Run workflow**, and enter the version without the `v` (for example `1.2.1`). It
     creates the tag `v1.2.1` on the commit it releases.
   - or push a version tag: `git tag v1.2.1 && git push origin v1.2.1`.

   The workflow checks the version matches the manifest and `CHANGELOG.md` (`scripts/release_notes.py`), runs every
   check, builds `ingress_proxy.zip` and checks it installs as `custom_components/ingress_proxy/`
   (`scripts/build_package.py`), and publishes the release on Gitea with the zip and notes: install steps, then that
   version's changelog section (`scripts/publish_gitea_release.py`). A version with a suffix (`-beta`) is published
   as a pre-release. If any check fails, nothing is published. Running it again for the same version updates the
   release and replaces its zip.

5. Gitea's push mirror then sends the commits and the new tag to GitHub, where the tag starts GitHub's own
   **Release** workflow (`.github/workflows/release.yml`), which runs the same checks and publishes the same
   release there. The Marketplace picks it up from GitHub. If GitHub is down, the mirror catches up later; the Gitea
   release is already done.

The Gitea workflows use no actions (`actions/checkout` and the like download from GitHub): each job runs in a plain
image from Docker Hub (`python:3.14-bookworm`, and `node:20-bookworm` for the panel script) and fetches the code from
Gitea with git, so testing and releasing keep working when GitHub can't be reached. Only `ruff` comes from PyPI. When
the runner's jobs can't reach Gitea at its public address (a runner on the same Docker network as Gitea, say), set an
Actions variable `GITEA_INTERNAL_URL` to the address they can reach (`http://gitea-server:3000`); both workflows and
the publish script use it.

Keep every tag on Gitea: the push mirror makes GitHub's tags match Gitea's, so a tag missing from Gitea is deleted on
GitHub, and the GitHub release on it turns into a draft.

## Known limitations / ideas

- Reply bodies aren't rewritten, so apps that link to their files from the root of the address need a base-path
  setting (see [Configuration](docs/Configuration.md#which-apps-work)).
- With **Remember the app's login** on, everyone who can open the panel shares one login to the app.
- Kept cookies ignore their own Path, Domain and expiry dates: a kept cookie is sent until the app deletes it.

## Reporting bugs

[Open an issue](https://github.com/soullink9/HA-Marketplace-Ingress-Proxy/issues/new/choose) with your Home Assistant version, the integration's
version, what you did and what you expected.

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
