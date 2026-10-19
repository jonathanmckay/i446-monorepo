# Fuchikoma iOS — native dtd + janus (+ dashboard) for iPhone

Swift port of the Android client (`../neg1n-watch/fuchikoma`). One app, three
tabs, a thin SwiftUI client over the Flask JSON APIs that already serve the
mobile web pages on Ix. No business logic lives in the app: every swipe is one
POST to the endpoint the web page calls, so server fixes land on the phone
without a rebuild.

| tab       | server                           | default endpoint                              |
|-----------|----------------------------------|-----------------------------------------------|
| dtd       | `tools/dtd/dtd.py` (5560)        | `http://100.114.46.109:5560`                  |
| janus     | `tools/janus/mobile.py` (5561)   | `http://100.114.46.109:5561`                  |
| dashboard | `tools/personal-dashboard` (5558)| `https://ix.tail9c51d5.ts.net/personal/` (WKWebView) |

HTTPS alternatives via `tailscale serve` on Ix: `https://ix.tail9c51d5.ts.net:8560`
(dtd) and `:8561` (janus). Not 5560/5561: tailscaled binding the Tailscale IP on
the same port stops Flask from re-binding `0.0.0.0` after a restart (2026-10-19).
Long-press any header to change endpoints (persisted in UserDefaults).

## Layout

```
project.yml                 XcodeGen spec → Fuchikoma.xcodeproj (gitignored)
Fuchikoma/                  SwiftUI app target (iOS 17+)
  RootView.swift            TabView, stores, settings sheet, foreground refresh + where beacon
  DtdStore/DtdView.swift    task list: leading swipe → done, trailing → start / +1d / ⏰
  JanusStore/JanusView.swift timeline: leading → log / stop+log / convert / fill, trailing → edit / split
  DashboardView.swift       WKWebView on the dashboard URL
  Platform.swift            UIKit-only bits (background task, haptics), guarded with #if
FuchikomaCore/              Swift package: Models, Parsers, Bodies, Api, Config
  Sources/CoreCheck         payload-fixture checks (`swift run CoreCheck`) — CommandLineTools has no XCTest
```

## Gestures

**dtd** — swipe right reveals **done** (tap it; a variable habit asks for its
value first). No full-swipe commit on purpose: SwiftUI's threshold is not
tunable and `/api/done` runs the real `/did`. Swipe left: ▶ start · 🗓 +1d ·
⏰ delay (remaining 地支 blocks today + 10/30/60m). Header **+** adds a task.
Pull to refresh. A failed completion/delay puts the row back with a persistent
retry toast; `/api/done` is idempotent by id, so a retry can never double-credit.

**janus** — swipe right: plain entry → log 分 (full swipe allowed; the server
ledger makes it idempotent), running entry → stop + log, calendar event →
convert to a Toggl entry, gap → fill sheet (all three reveal-then-tap). Swipe
left on an entry → edit (desc/time/project) or split top/bottom. Already-logged
rows are inert. Header **+** starts something "now".

Header: 分 total for the tab, then the endpoint or the last error on the
second line (a wedged tailnet must not look like an empty day).

## Build / install

Prereqs (one-time): Xcode from the Mac App Store; an Apple Developer Program
membership if you want the install to outlive 7 days (free Apple ID = 7-day
provisioning, re-plug weekly; paid = 1 year per cable install, 90 days per
TestFlight build); Developer Mode on the iPhone (Settings › Privacy & Security).

```
brew install xcodegen            # already on Straylight
cd mobile/fuchikoma-ios
xcodegen generate                # writes Fuchikoma.xcodeproj
open Fuchikoma.xcodeproj         # Signing & Capabilities → Team; plug in phone; ⌘R
```

Checks without Xcode: `cd FuchikomaCore && swift run CoreCheck` (parsers and
request bodies against captured server payloads), and a macOS typecheck of the
UI (`swiftc -typecheck … Fuchikoma/*.swift`, see git log 2026-10-19).

## Server-side contract notes (2026-10-19)

- `/api/done` returns `{ok, already:true}` for an id already in
  completed-today instead of re-running did-fast (phone locked mid-POST → retry).
- `/api/where` honours `X-Forwarded-For` from loopback, so the where beacon
  works through `tailscale serve`.
- janus `mobile.py` runs `threaded=True` so a 45 s `/api/log` no longer blocks
  `/api/timeline`.

## Next (native-only wins, each needs the paid developer account)

- Live Activity / Dynamic Island for the running Toggl timer (`Text(timerInterval:)`;
  stopping it from the desktop needs an APNs push from Ix with a .p8 key).
- Lock-screen widget: today's 分 (refreshes on iOS's schedule; stale when the
  tailnet is dormant).
- App Intents: "Hey Siri, did 0t" → `/api/done`.
