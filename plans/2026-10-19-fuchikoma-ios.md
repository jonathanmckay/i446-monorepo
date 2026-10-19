# Fuchikoma iOS — dtd + janus + dashboard as a native iPhone app

Date: 2026-10-19 (drafted on a plane; iPhone offline, Xcode not yet installed)

## What exists

- Three Flask apps on Ix, Tailscale-only: dtd (5560), janus mobile (5561),
  personal dashboard (5558). Each embeds a phone-first HTML UI, already tagged
  `apple-mobile-web-app-capable`, opened from the iPhone home screen over
  plain `http://ix:PORT`.
- A native Android client, Fuchikoma (`mobile/neg1n-watch/fuchikoma`, ~940
  lines Kotlin): thin client over the same JSON endpoints, no business logic.
- `tailscale serve` on Ix already fronted the dashboard with HTTPS.

## Decision

Port Fuchikoma to SwiftUI (`mobile/fuchikoma-ios`), same thin-client contract:
dtd and janus as native lists with swipe actions; the dashboard as a WKWebView
tab. Keep the Flask apps as the single source of logic so the web pages, the
Android app and the iOS app stay in lockstep by construction.

Why native at all (rubber-duck critique, 2026-10-19): for a plain iPhone the
web home-screen apps already cover ~80%. What native adds, in order of value:
no Safari edge-back gesture fighting swipes, instant launch from cached state,
haptics, and the three things only an app can do — Live Activity for the
running Toggl timer, a lock-screen 分 widget, Siri/App Intents for `/did`.
Those three need the paid developer account; without that ambition, PWA
hardening (manifest + service worker over the new HTTPS endpoints) is the
cheaper answer.

## Done 2026-10-19

- `FuchikomaCore` Swift package: Models / Parsers / Bodies / Api / Config, mirror
  of the Kotlin files. `swift run CoreCheck` passes on macOS with CommandLineTools
  (no XCTest there), same payload fixtures as `ModelsTest.kt` plus HTTP-verdict
  and config checks.
- SwiftUI app target (RootView, Dtd*, Janus*, Dashboard, Settings, Toast,
  Platform): typechecks on macOS with UIKit bits `#if`-guarded. NOT yet built
  for iOS — needs Xcode.
- XcodeGen `project.yml` → `Fuchikoma.xcodeproj` generated; app icon (分 on
  dark, four Fuchikoma legs).
- Server: `/api/done` idempotent by id; `/api/where` honours X-Forwarded-For
  from loopback; janus `threaded=True`. Regression test
  `tools/dtd/test_dtd_done_idempotent_and_forwarded_peer.py`. Deployed to Ix.
- `tailscale serve` HTTPS for dtd/janus at `:8560` / `:8561` (first try on
  5560/5561 collided with Flask's own bind after restart; moved).

## Design choices worth remembering

- dtd done = reveal-then-tap, not full-swipe commit. SwiftUI's full-swipe
  threshold is not tunable; Android deliberately used 160 dp + 6× escape
  velocity to stop accidental completes. Revisit with a custom DragGesture row
  if the extra tap grates after a week of use.
- janus plain-entry log keeps full-swipe (server ledger makes it idempotent).
- POSTs run inside `beginBackgroundTask` so a lock mid-`/did` does not kill them.
- Defaults are the Tailscale IP over http (like Android, routes from cellular);
  HTTPS hostnames are one long-press away in Settings. ATS global exception is
  on because ATS cannot except an IP literal.

## Remaining (needs JM)

1. Install Xcode: App Store › Xcode › Get, or `! sudo mas install 497799835`
   (~4 GB at plane speeds ≈ 40 min; mas needs sudo on this macOS). ~1 min.
2. Apple Developer Program enrollment ($99/yr) at developer.apple.com/enroll
   with the Apple ID on this Mac. Skip only if a 7-day re-plug cadence is
   acceptable. ~10 min, approval up to 48 h.
3. iPhone: Settings › Privacy & Security › Developer Mode on; cable; trust. ~3 min.
4. `cd mobile/fuchikoma-ios && xcodegen generate && open Fuchikoma.xcodeproj`,
   pick Team, ⌘R. First build will surface iOS-only compile issues the macOS
   typecheck could not see; expect a short fix loop.

## Later

- Live Activity (running timer) with APNs stop-push from Ix (.p8 key on Ix).
- Lock-screen widget for today's 分 (WidgetKit timeline; stale when tailnet dormant).
- App Intents: "did <habit>" → `/api/done`.
- PWA hardening of the web pages regardless (manifest, icons, `touch-action`
  on rows, service worker for the shell) now that HTTPS exists.
