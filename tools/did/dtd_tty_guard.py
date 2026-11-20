#!/usr/bin/env python3
"""dtd_tty_guard.py: un-stick dtd when its tty is left in cooked mode.

Bug 2026-11-19 ("dtd froze"): fzf was alive and idle, but the pane's tty was
in canonical mode (icanon isig -echo, intr/quit undefined: the state dtd's
edit script leaves behind), so no key reached fzf until Enter and its output
was post-processed into stale fragments. Root cause NOT found: six pty
reproductions (detached drains racing fzf's Resume, zsh restoring a saved
tty state, the live edit script + real form under real fzf) all ended raw.
So this is handling plus evidence, per systematic-debugging's "no root
cause" branch. If the log below gains a record, start from it.

How it is used: dtd's watcher sees `icanon` on the tty and POSTs
`transform(dtd_tty_guard.py --check ...)` to fzf's listen port. fzf runs
POSTed actions from its main loop, so the check can never run while an
execute() child (edit form, prompt, survey) legitimately owns a cooked tty,
and it runs before fzf's own 1 s Pause(false). Cooked at that moment
therefore means stuck. (fzf's GET endpoint is no use for this: it answers
instantly even inside execute().)

On a stuck tty --check: appends an evidence record to --log, discards the
keys queued while cooked (they would otherwise replay into fzf: alt-enter
closes tasks), puts the tty back in raw mode the way fzf's Resume does
(x/term MakeRaw), and prints fzf actions on stdout to repaint. The mode is
set directly instead of through an fzf execute() round trip because Pause()
would restore intr=^C with ISIG on while the user is hitting keys at a
frozen screen (the 2026-10-22 ^C-kills-fzf window).

Exit status is always 0; stdout is only ever fzf actions (or empty).
"""
import argparse
import os
import subprocess
import sys
import termios
import time

# golang.org/x/term MakeRaw, which is what fzf's LightRenderer.Resume applies
_IFLAG_OFF = (termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP
              | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
_LFLAG_OFF = termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN


def is_cooked(attrs) -> bool:
    return bool(attrs[3] & termios.ICANON)


def make_raw(attrs):
    """Return attrs changed exactly as MakeRaw changes them (cc otherwise kept)."""
    out = list(attrs)
    out[0] &= ~_IFLAG_OFF
    out[1] &= ~termios.OPOST
    out[2] = (out[2] & ~(termios.CSIZE | termios.PARENB)) | termios.CS8
    out[3] &= ~_LFLAG_OFF
    cc = list(out[6])
    cc[termios.VMIN] = 1
    cc[termios.VTIME] = 0
    out[6] = cc
    return out


def _cc(attrs, idx) -> str:
    c = attrs[6][idx]
    if c in (b"\xff", b"\x00"):
        return "undef"
    return "^" + chr(c[0] ^ 0x40) if c[0] < 0x20 else repr(c)


def describe(attrs) -> str:
    lf = attrs[3]
    bits = [("icanon", termios.ICANON), ("isig", termios.ISIG), ("iexten", termios.IEXTEN), ("echo", termios.ECHO)]
    flags = " ".join(n if lf & b else "-" + n for n, b in bits)
    return (f"lflags[{flags}] opost={'on' if attrs[1] & termios.OPOST else 'off'} "
            f"icrnl={'on' if attrs[0] & termios.ICRNL else 'off'} "
            f"intr={_cc(attrs, termios.VINTR)} quit={_cc(attrs, termios.VQUIT)} susp={_cc(attrs, termios.VSUSP)} "
            f"raw=iflag:{attrs[0]:#x},oflag:{attrs[1]:#x},cflag:{attrs[2]:#x},lflag:{attrs[3]:#x}")


def _tail(path, n):
    try:
        with open(path, errors="replace") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def evidence(fd, attrs, timing, events, dtd_id) -> str:
    try:
        tty = os.ttyname(fd)
    except OSError:
        tty = "?"
    lines = [f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} epoch={time.time():.3f} dtd={dtd_id or '?'} tty={tty}",
             "stuck: " + describe(attrs)]
    try:
        ps = subprocess.run(["ps", "-o", "pid,ppid,pgid,tpgid,stat,etime,command", "-t", tty.replace("/dev/", "")],
                            capture_output=True, text=True, timeout=3).stdout
        lines += ["-- processes on the tty"] + [l[:200] for l in ps.splitlines()]
    except (OSError, subprocess.SubprocessError) as e:
        lines.append(f"-- ps failed: {e}")
    if timing:
        lines.append("-- timing log tail (epoch, event, detail)")
        lines += [l.rstrip("\n")[:200] for l in _tail(timing, 60)]
    if events:
        lines.append("-- events tail")
        lines += [l.rstrip("\n")[:160] for l in _tail(events, 12) if "\trender\t" not in l]
    return "\n".join(lines) + "\n"


def check(tty_path, log, timing, events, dtd_id, hdr, hdrgen) -> str:
    """Heal a stuck tty. Returns the fzf actions to print ('' when nothing was wrong)."""
    try:
        fd = os.open(tty_path, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return ""
    try:
        attrs = termios.tcgetattr(fd)
        if not is_cooked(attrs):
            return ""
        if log:
            try:
                os.makedirs(os.path.dirname(log) or ".", exist_ok=True)
                with open(log, "a") as f:
                    f.write(evidence(fd, attrs, timing, events, dtd_id))
            except OSError:
                pass
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSANOW, make_raw(attrs))
        termios.tcflush(fd, termios.TCIFLUSH)
    except termios.error:
        return ""
    finally:
        os.close(fd)
    actions = "clear-screen"
    if hdr and hdrgen:
        try:
            with open(hdr, "w") as f:
                f.write("⚠ keyboard was stuck (tty cooked): recovered, keys typed meanwhile dropped\n")
            actions += f"+transform-header({hdrgen})"
        except OSError:
            pass
    return actions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--tty", default="/dev/tty")
    ap.add_argument("--log")
    ap.add_argument("--timing")
    ap.add_argument("--events")
    ap.add_argument("--id")
    ap.add_argument("--hdr")
    ap.add_argument("--hdrgen")
    a = ap.parse_args()
    if a.check:
        sys.stdout.write(check(a.tty, a.log, a.timing, a.events, a.id, a.hdr, a.hdrgen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
