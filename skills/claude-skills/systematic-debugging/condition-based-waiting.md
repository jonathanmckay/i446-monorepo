# Condition-Based Waiting

Part of `systematic-debugging`. Adapted from obra/superpowers (MIT).

Flaky tests and flaky scripts guess at timing: `sleep 2`, `time.sleep(0.5)`,
"give Toggl a second". They pass on a quiet machine and fail under load, on
Ix, or when a daemon is busy.

**Rule:** wait for the condition you care about, not for a guess at how long
it takes.

## Use when

- A test or script has an arbitrary delay.
- Something passes sometimes and fails under load or in parallel.
- You are waiting for an async write (Toggl propagation, the excel-http
  journal, a launchd kickstart, a file Syncthing is carrying to Ix).

## Do not use when

- The timing itself is the behaviour under test (a debounce, the 60-second
  "timer just started" guard). Then keep the delay and write down why.

## The pattern

```python
# Before: a guess
time.sleep(2)
assert cache_file.exists()

# After: the condition, with a ceiling
def wait_for(pred, timeout=10.0, step=0.1):
    t0 = time.monotonic()
    while not pred():
        if time.monotonic() - t0 > timeout:
            raise TimeoutError("condition not met")
        time.sleep(step)

wait_for(lambda: cache_file.exists())
```

Shell equivalent:

```bash
until grep -q "ready" server.log; do sleep 0.5; done
```

## Quick patterns

| Waiting for | Poll |
|---|---|
| A file to appear or change | `path.exists()`, mtime, or a hash |
| A service to come up | bounded `curl` on its health endpoint |
| A process to exit | `kill -0 pid` fails |
| A remote write to land | read it back through a different path (the API, the persisted artifact) |
| Syncthing to reach Ix | `ssh ix md5 -q <file>` equals the local md5 |

Always bound the wait and fail loudly on timeout; an unbounded poll is the
hang the `/bug` skill warns about.
