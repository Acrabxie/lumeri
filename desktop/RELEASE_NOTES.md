# Lumeri 0.1.0 — Desktop Preview

The first double-click build of Lumeri. Runs on macOS and Windows with **no
Python, no Node, no configuration**. Just download, install, open.

This is a **preview build**: the interface is real, sign-in and generation
run in the hosted product at [lumeri.ai](https://lumeri.ai). It exists so
you can see the shape of the tool before we open the backend.

## Downloads

| Platform         | File                                    |
| ---------------- | --------------------------------------- |
| macOS (Apple)    | `Lumeri-0.1.0-mac-arm64.dmg`            |
| macOS (Intel)    | `Lumeri-0.1.0-mac-x64.dmg`              |
| Windows (x64)    | `Lumeri-0.1.0-win-x64.exe`              |

macOS builds are unsigned — first launch, right-click → Open, or run
`xattr -dr com.apple.quarantine /Applications/Lumeri.app`.

## What's inside

- The full Lumeri v3 interface: timeline, asset grid, media library,
  plan-mode bar.
- The brand: cyan double-arc mark, dark theme.
- Local HTTP layer that serves the UI and returns preview-mode responses
  for backend calls.

## What's not yet

- Real AI turn execution (routes to the hosted backend).
- Sign-in (email / Google) — routes to lumeri.ai.
- Auto-update.

## Next

We're shipping the hosted backend and the promo film in parallel. Star the
repo if you want to be notified when the full product opens.
