# Lumeri Desktop (Preview)

Local Electron shell that bundles the Lumeri v3 UI into a double-click app —
no Python, no Node install, no configuration.

This is a **preview build**: the interface is real, backend calls are stubbed.
Sign-in and full generation happen in the hosted product.

## Develop

```bash
cd desktop
npm install
npm start
```

## Build installers

```bash
npm run build:mac   # → dist/Lumeri-0.1.0-mac-arm64.dmg (+ x64)
npm run build:win   # → dist/Lumeri Setup 0.1.0.exe
npm run build:all   # both
```

Windows cross-compile from macOS needs Wine or a Windows runner
(recommended: GitHub Actions).

## Layout

- `main.js` — Electron entry, spins up an in-process HTTP server that
  serves `app-assets/v3/` and stubs the `/auth/*`, `/sessions/*`,
  `/media-library/*` endpoints.
- `stub-server.js` — the tiny server + API stubs.
- `scripts/copy-assets.js` — copies `../static/v3` into `app-assets/v3`
  before every build.
- `build/` — installer resources (icons, background).

## What's stubbed

| Endpoint                          | Behavior in preview |
| --------------------------------- | ------------------- |
| `GET  /auth/session`              | `{ authenticated: false, mode: "preview" }` |
| `POST /auth/email/*`, `/google/*` | 501 with a friendly message pointing to lumeri.ai |
| `POST /sessions`                  | Returns a synthetic `preview-<uuid>` session id |
| `GET  /sessions/*/timeline`       | Empty timeline |
| `GET  /sessions/*/assets`         | Empty assets |
| `POST /sessions/*/turn`           | 501 (needs the hosted backend) |
| `GET  /media-library/list`        | Empty list |
