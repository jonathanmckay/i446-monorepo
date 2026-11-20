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


# 2026-11-20: ~1 s goal. Straylight rsyncs each screenshot straight to
# ~/.cache/shots-direct; Syncthing's copy lands 7-15 s later.
def _dirs(tmp_path):
    d = {n: tmp_path / n for n in ("clips", "shots", "direct")}
    for p in d.values():
        p.mkdir()
    return d


def test_direct_copy_is_taken_at_once_even_right_after_the_paste(tmp_path):
    d = _dirs(tmp_path)
    pt = hook.paste_time(SHOT_REMOTE)
    want = _shot(d["direct"], "2026-11-20_093612--ss.png", pt - 3)
    slept = []
    kind, path, age = hook.resolve(SHOT_REMOTE, clips_dir=str(d["clips"]), shots_dir=str(d["shots"]),
                                   direct_dir=str(d["direct"]), now=lambda: pt + 0.5,
                                   sleep=lambda dt: slept.append(dt))
    assert (kind, path, age, slept) == ("shot", str(want), 3, [])


def test_newest_across_both_dirs_wins(tmp_path):
    # Burst: the push of the latest shot failed, the earlier one sits in direct.
    d = _dirs(tmp_path)
    pt = hook.paste_time(SHOT_REMOTE)
    _shot(d["direct"], "2026-11-20_093608--ss.png", pt - 7)
    want = _shot(d["shots"], "2026-11-20_093612--ss.png", pt - 3)
    out = hook.resolve(SHOT_REMOTE, clips_dir=str(d["clips"]), shots_dir=str(d["shots"]),
                       direct_dir=str(d["direct"]), now=lambda: pt + 10)
    assert out[:2] == ("shot", str(want))


def test_syncthing_only_copy_waits_for_settle_so_a_newer_one_can_land(tmp_path):
    d = _dirs(tmp_path)
    pt = hook.paste_time(SHOT_REMOTE)
    _shot(d["shots"], "2026-11-20_093608--ss.png", pt - 7)   # previous shot of a burst
    clock = {"t": pt + 0.5}

    def sleep(dt):
        clock["t"] += dt
        if clock["t"] >= pt + 2 and not list(d["direct"].iterdir()):
            _shot(d["direct"], "2026-11-20_093612--ss.png", pt - 3)

    kind, path, age = hook.resolve(SHOT_REMOTE, clips_dir=str(d["clips"]), shots_dir=str(d["shots"]),
                                   direct_dir=str(d["direct"]), now=lambda: clock["t"], sleep=sleep)
    assert path.endswith("093612--ss.png") and age == 3


def test_partial_rsync_temp_is_ignored(tmp_path):
    d = _dirs(tmp_path)
    pt = hook.paste_time(SHOT_REMOTE)
    _shot(d["direct"], ".2026-11-20_093612--ss.png.Ab12Cd", pt - 3)
    assert hook.newest_shot(pt, str(d["shots"]), str(d["direct"])) is None


def test_prune_direct_drops_day_old_files(tmp_path):
    old = _shot(tmp_path, "2026-11-18_093612--ss.png", 1_000_000)
    new = _shot(tmp_path, "2026-11-20_093612--ss.png", 2_000_000 - 60)
    hook.prune_direct(str(tmp_path), now=lambda: 2_000_000)
    assert not old.exists() and new.exists()
