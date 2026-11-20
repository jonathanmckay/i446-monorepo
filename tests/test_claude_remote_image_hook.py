"""Regression: a screenshot pasted in cmux on Straylight into an ssh session on
Ix arrives as a /var/folders/.../T/clipboard-*.png path that does not exist on
Ix (2026-11-11, /bug with a clipboard path). The hook must point Claude at the
Syncthing copy in ~/files/i447/clips/ instead of letting Read fail."""
import importlib.util
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(__file__), "..", "scripts", "claude-remote-image-hook.py")
spec = importlib.util.spec_from_file_location("hook", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

REMOTE = "/var/folders/vw/1y3l3cdx1b9d0v116jl93z9r0000gn/T/clipboard-2026-11-11-081125-E99609D0.png"


def test_rewrites_missing_straylight_path_to_synced_copy(tmp_path):
    local = tmp_path / "clipboard-2026-11-11-081125-E99609D0.png"
    local.write_bytes(b"\x89PNG")
    ctx = hook.build_context(f"/bug {REMOTE}", exists=lambda p: False, clips_dir=str(tmp_path))
    assert str(local) in ctx
    assert f"Use Read on {local}" in ctx
    assert "does not exist on this machine" in ctx


def test_no_image_path_emits_nothing():
    assert hook.build_context("fix the inbox counter", exists=lambda p: False) == ""


def test_existing_local_path_is_left_alone(tmp_path):
    # On Straylight itself the file exists, so the hook must stay silent.
    assert hook.build_context(REMOTE, exists=lambda p: True, clips_dir=str(tmp_path)) == ""


def test_matches_private_prefix_jpeg_and_dedupes():
    p = "/private/var/folders/ab/cd/T/clipboard-2026-11-11-090000-ABC.JPEG"
    assert hook.remote_image_paths(f"{p} and again {p}", exists=lambda _: False) == [p]


def test_reports_timeout_when_sync_never_arrives(tmp_path):
    ctx = hook.build_context(REMOTE, exists=lambda p: False, clips_dir=str(tmp_path),
                             waiter=lambda local: False)
    assert "did not sync" in ctx
    assert "install-clip-image-sync.sh" in ctx


def test_wait_keeps_polling_while_syncthing_tmp_present(tmp_path):
    local = tmp_path / "clipboard-x.png"
    tmp = tmp_path / ".syncthing.clipboard-x.png.tmp"
    tmp.write_bytes(b"")
    clock = {"t": 0.0}
    ticks = {"n": 0}

    def sleep(_):
        ticks["n"] += 1
        clock["t"] += 0.5
        if ticks["n"] == 3:  # Syncthing finishes after the deadline passed
            tmp.unlink()
            local.write_bytes(b"\x89PNG")

    assert hook.wait_for(str(local), wait_s=0.5, step=0.5, now=lambda: clock["t"], sleep=sleep)


def test_cli_emits_hook_json(tmp_path):
    local = tmp_path / "clipboard-2026-11-11-081125-E99609D0.png"
    local.write_bytes(b"\x89PNG")
    env = dict(os.environ, CLIP_DST=str(tmp_path), CLIP_WAIT_S="1")
    r = subprocess.run([sys.executable, HOOK], input=json.dumps({"prompt": REMOTE}),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert str(local) in out["hookSpecificOutput"]["additionalContext"]


# 2026-11-20: the Straylight clip agent was never installed, so no clip with the
# pasted name ever arrived; the screenshot itself was in ~/screenshots 23 s
# before the paste and the session had to find it by hand.
SHOT_REMOTE = "/var/folders/vw/1y3l3cdx1b9d0v116jl93z9r0000gn/T/clipboard-2026-11-20-093615-CFDA9C6C.png"


def _shot(d, name, when):
    p = d / name
    p.write_bytes(b"\x89PNG")
    os.utime(p, (when, when))
    return p


def test_falls_back_to_screenshot_taken_just_before_paste(tmp_path):
    clips, shots = tmp_path / "clips", tmp_path / "shots"
    clips.mkdir(); shots.mkdir()
    pt = hook.paste_time(SHOT_REMOTE)
    _shot(shots, "2026-11-20_090000--ss.png", pt - 2175)   # too old
    want = _shot(shots, "2026-11-20_093552--ss.png", pt - 23)
    _shot(shots, "2026-11-20_093530--ss.png", pt - 45)     # older, in window
    _shot(shots, "2026-11-20_093700--ss.png", pt + 45)     # taken after the paste
    ctx = hook.build_context(SHOT_REMOTE, exists=lambda p: False,
                             clips_dir=str(clips), shots_dir=str(shots))
    assert f"Use Read on {want}" in ctx
    assert "taken 23 s before the paste" in ctx


def test_exact_clip_wins_over_screenshot(tmp_path):
    clips, shots = tmp_path / "clips", tmp_path / "shots"
    clips.mkdir(); shots.mkdir()
    pt = hook.paste_time(SHOT_REMOTE)
    _shot(shots, "2026-11-20_093552--ss.png", pt - 23)
    clip = clips / os.path.basename(SHOT_REMOTE)
    clip.write_bytes(b"\x89PNG")
    assert hook.resolve(SHOT_REMOTE, clips_dir=str(clips), shots_dir=str(shots))[:2] == ("clip", str(clip))


def test_waits_for_a_screenshot_still_in_transit(tmp_path):
    clips, shots = tmp_path / "clips", tmp_path / "shots"
    clips.mkdir(); shots.mkdir()
    pt = hook.paste_time(SHOT_REMOTE)
    clock = {"t": pt + 6}  # prompt submitted 6 s after the paste (2026-11-19 13:02:05)

    def sleep(dt):
        clock["t"] += dt
        if clock["t"] >= pt + 20 and not list(shots.iterdir()):
            _shot(shots, "2026-11-20_093610--ss.png", pt - 5)

    kind, path, age = hook.resolve(SHOT_REMOTE, clips_dir=str(clips), shots_dir=str(shots),
                                   now=lambda: clock["t"], sleep=sleep)
    assert (kind, age) == ("shot", 5) and path.endswith("093610--ss.png")
    assert clock["t"] < pt + 21


def test_gives_up_at_grace_after_paste_not_wait_s_from_now(tmp_path):
    clips, shots = tmp_path / "clips", tmp_path / "shots"
    clips.mkdir(); shots.mkdir()
    pt = hook.paste_time(SHOT_REMOTE)
    clock = {"t": pt + 300}  # a prompt typed for five minutes must not block at all
    slept = []
    out = hook.resolve(SHOT_REMOTE, clips_dir=str(clips), shots_dir=str(shots),
                       now=lambda: clock["t"], sleep=lambda dt: slept.append(dt))
    assert out == (None, None, None) and slept == []
    ctx = hook.build_context(SHOT_REMOTE, exists=lambda p: False, clips_dir=str(clips),
                             shots_dir=str(shots), resolver=lambda r, **k: out)
    assert "did not sync" in ctx
