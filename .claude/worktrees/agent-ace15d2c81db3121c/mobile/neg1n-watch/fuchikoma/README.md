# Fuchikoma — native Android dtd + janus

One APK, two surfaces, one launcher icon (long-press it for a direct `dtd` or
`janus` shortcut). A thin Kotlin client over the Flask JSON APIs that already
serve the mobile web pages on Ix:

| tab   | server                    | port |
|-------|---------------------------|------|
| dtd   | `tools/dtd/dtd.py`        | 5560 |
| janus | `tools/janus/mobile.py`   | 5561 |

No business logic lives in the app. Every swipe is one POST to the same
endpoint the web page calls, so parity with the web versions is by
construction and server fixes land on the phone without a rebuild.

## Gestures

**dtd** — right-swipe completes (real `/did`: Todoist close + Neon write; a
variable habit asks for its value first). Left-swipe opens ▶ start · 🗓 +1d ·
⏰ delay (remaining 地支 blocks today + 10/30/60m). FAB adds a task. Pull to
refresh. A failed completion/delay puts the row back with a retry Snackbar.

**janus** — right-swipe: entry → log 分, running entry → stop + log, calendar
event → convert to a Toggl entry, gap → fill dialog. Left-swipe on an entry →
edit (desc/time/project) or split top/bottom. Already-logged rows are inert.
FAB starts something "now".

Header: 分 total for the tab, then the endpoint or the last error on the
second line (a wedged tailnet must not look like an empty day). Long-press the
header to change either endpoint.

## Build / install

```
./install-fuchikoma.sh <phone ip:port>   # from mobile/neg1n-watch
```

JDK: `/opt/homebrew/opt/openjdk@17` (brew; `java_home` does not see it).
Unit tests: `./gradlew :fuchikoma:testDebugUnitTest` — parsers and request
bodies against captured server payloads.
