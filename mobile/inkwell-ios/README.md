# inkwell-ios — three iPhone apps: dtd, janus, Inkwell

Swift port of the Android client, Fuchikoma (`../neg1n-watch/fuchikoma`). One
source tree (`App/`), three XcodeGen app targets with their own home-screen
icons (2026-10-19): **dtd** (task list), **janus** (timeline), **Inkwell**
(vault subset + capture, plus the dashboard web view). Thin SwiftUI clients over
the Flask JSON APIs that already serve the mobile web pages on Ix. No business logic lives in the app: every swipe is one
POST to the endpoint the web page calls, so server fixes land on the phone
without a rebuild.

| app     | bundle id          | compile flag | server                                        |
|---------|--------------------|--------------|-----------------------------------------------|
| dtd     | com.mckay.dtd      | APP_DTD      | `tools/dtd/dtd.py` (5560) `http://100.114.46.109:5560` |
| janus   | com.mckay.janus    | APP_JANUS    | `tools/janus/mobile.py` (5561) `http://100.114.46.109:5561` |
| Inkwell | com.mckay.inkwell  | APP_INKWELL  | vault: `tools/vault-mobile` (Funnel :8443/vault); dash: `https://ix.tail9c51d5.ts.net/personal/` (WKWebView) |
| vault     | `tools/vault-mobile` (5571)      | `https://ix.tail9c51d5.ts.net:8443/vault` (Funnel, bearer token; works with the VPN off) |

HTTPS alternatives via `tailscale serve` on Ix: `https://ix.tail9c51d5.ts.net:8560`
(dtd) and `:8561` (janus). Not 5560/5561: tailscaled binding the Tailscale IP on
the same port stops Flask from re-binding `0.0.0.0` after a restart (2026-10-19).
Long-press any header to change endpoints (persisted in UserDefaults).

## Layout

```
project.yml                 XcodeGen spec → Inkwell.xcodeproj (gitignored)
Inkwell/                    SwiftUI app target (iOS 17+)
  RootView.swift            TabView, stores, settings sheet, foreground refresh + where beacon
  DtdStore/DtdView.swift    task list: leading swipe → done, trailing → start / +1d / ⏰
  JanusStore/JanusView.swift timeline: leading → log / stop+log / convert / fill, trailing → edit / split
  DashboardView.swift       WKWebView on the dashboard URL
  VaultTabStore/VaultView   offline vault subset (recent / browse / search) + capture sheet; read-only notes
  VaultIntents.swift        "Capture to Fuchikoma" App Intent: Action Button / Siri / lock screen, offline
VaultCore/                  Swift package: SQLite+FTS5 store, manifest set-diff sync, capture outbox
  Sources/VaultCheck        store/sync/outbox checks against a URLProtocol stub (`swift run VaultCheck`)
  Platform.swift            UIKit-only bits (background task, haptics), guarded with #if
InkwellCore/              Swift package: Models, Parsers, Bodies, Api, Config
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
cd mobile/inkwell-ios
xcodegen generate                # writes Inkwell.xcodeproj (three schemes)
# build + install all three over the cable (team is pinned in project.yml):
UDID=$(xcrun devicectl list devices | awk '/connected/{print $3}' | head -1)
for s in dtd janus Inkwell; do
  xcodebuild -project Inkwell.xcodeproj -scheme $s -destination "id=$UDID" \
    -allowProvisioningUpdates -derivedDataPath build/DerivedData build
  xcrun devicectl device install app --device $UDID build/DerivedData/Build/Products/Debug-iphoneos/$s.app
done
# free Apple ID: profiles expire after 7 days → rerun the loop
```

Checks without Xcode: `cd InkwellCore && swift run CoreCheck` (parsers and
request bodies against captured server payloads), `cd VaultCore && swift run VaultCheck`
(offline store, FTS, outbox, sync loop against a stubbed server), and a macOS
typecheck of the UI:

```
swiftc -typecheck -I InkwellCore/.build/arm64-apple-macosx/debug/Modules \
  -I VaultCore/.build/arm64-apple-macosx/debug/Modules Fuchikoma/*.swift
```

## Vault tab (2026-10-19)

The phone holds the subset declared in `vault/z_meta/phone-subset.md` (~9k
notes, 24 MB) in SQLite; reads and search are local and instant. Sync on
foreground is one `/manifest` request with `If-None-Match` (a 304 when nothing
changed), then a full set-diff: changed files are fetched 6 at a time with a
sha check, deleted files drop out. Captures never edit existing notes: the
pencil (or the App Intent) writes to a local outbox, and the next sync POSTs
each one to `/drop`, which files it as `z_ibx/phone/<stamp>-<id4>-<device>.md`
for `/notes` to route. Setup: long-press the header → paste the device token
(`ssh ix 'cd ~/i446-monorepo/tools/vault-mobile && python3 server.py token imago-c'`).
v1 renders notes as plain monospaced text; wikilinks are listed under the body
and resolve locally ("not on phone" for anything outside the subset).

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
