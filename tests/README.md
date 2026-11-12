# Tests

Run everything from the repository root. Each test is a plain Python script, not a pytest suite: it prints what it
checks and exits non-zero if anything failed. These are the commands the Test workflow runs:

```sh
python3 tests/test_integration.py
python3 tests/test_release.py
```

`test_live_proxy.py` isn't in the workflows: it needs aiohttp (`pip install aiohttp`). Run it by hand after any
change to `proxy.py` or `client.py`, and before every release.

| Script | What it checks |
|---|---|
| `test_integration.py` | Checks for the Ingress Proxy integration that need nothing but Python |
| `test_live_proxy.py` | End-to-end check of the proxy with real HTTP and websockets: a browser, the proxy and a fake app, all local |
| `test_release.py` | The release scripts in `scripts/`: the version check and notes, building and checking the zip, publishing to a fake Gitea server, and working out which releases GitHub still needs |

[CONTRIBUTING.md](../CONTRIBUTING.md#checking-a-change) says which to run for which change, and how long each takes.
The tests stand in for Home Assistant (`stubs.py`), so they can't prove the integration works on a real Home
Assistant: try changes on a real installation too.
