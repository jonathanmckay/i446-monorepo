"""Value-tag 媒分 credits on timer stop.

Toggl tags ``-1`` / ``-2`` / ``-3`` are media value tags: the entry's MINUTES
go to the tag's own 0n column (via neon-cols, never hardcoded) and the
sheet's formulas turn them into 媒分 (0.1/m, 0.5/m, 1/m). Until 2026-09-16
only tags added through janus's edit flow were credited (it queues a
"pending" credit and resolves it once the entry stops), so an entry started
with an explicit ``#-2`` (``/do through airport @i444 #-2``) and stopped by
``/did`` earned nothing. JM: "if there is a tag on the task those points get
recorded".

``credit_entry`` is called from ``toggl_api.stop_timer`` — the one choke
point every stop path (did-fast, /done, /tg stop, the MCP tool, janus)
passes through — so any explicitly tagged entry is credited exactly once:

* tags that a ``/tg`` SHORTCODE auto-attaches (新闻 → -3, hiit → -2, …) are
  NOT credited: those habits already earn their own points, and blanket
  crediting would double count (the same rule janus applies).
* the journal is janus's own ``janus-tag-credits.json`` (``credited`` keys
  ``"<entry id>:<tag>"``), so janus and this hook can never both pay the
  same credit; a matching janus ``pending`` entry is resolved here.
* everything is best-effort: a failure never blocks the stop.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

VALUE_TAGS = ("-1", "-2", "-3")
STATE_PATH = Path.home() / ".local/state/jm/janus-tag-credits.json"
TG_FAST = Path.home() / "i446-monorepo/tools/tg/tg-fast.py"
LIB = Path.home() / "i446-monorepo/lib"
TZ = ZoneInfo("America/Los_Angeles")

_shortcodes: dict | None = None


def _auto_tags_for(desc: str) -> set[str] | None:
    """Tags /tg attaches on its own for this description (a SHORTCODES key),
    or None when the shortcode table could not be loaded (→ don't credit;
    the pre-2026-09-16 behaviour, never a double count)."""
    global _shortcodes
    if _shortcodes is None:
        try:
            spec = importlib.util.spec_from_file_location("tg_fast_sc", TG_FAST)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            _shortcodes = {k.lower(): set(v[1]) for k, v in mod.SHORTCODES.items()}
        except Exception as e:  # noqa: BLE001
            print(f"WARN tag_credits: cannot load SHORTCODES ({e}); no credit", file=sys.stderr)
            _shortcodes = {}
            return None
    return _shortcodes.get(desc.strip().lower(), set())


def _load_state(today: _dt.date) -> dict:
    try:
        d = json.loads(STATE_PATH.read_text())
        if d.get("date") == today.isoformat():
            d.setdefault("credited", [])
            d.setdefault("pending", [])
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"date": today.isoformat(), "credited": [], "pending": []}


def _save_state(d: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(d, ensure_ascii=False))
    except OSError:
        pass


def _tag_col(tag: str) -> str:
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    from neon import cols as neon_cols
    try:
        return neon_cols.col("0n", tag)
    except KeyError:
        return neon_cols.col("0n", f"{tag}.0")


def _append(sheet: str, col: str, *, date: str, value: str, src: str) -> None:
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    from neon import excel as neon_excel
    neon_excel.append(sheet, col, date=date, value=value, src=src)


def entry_minutes(entry: dict, now: _dt.datetime | None = None) -> int:
    dur = entry.get("duration")
    if isinstance(dur, (int, float)) and dur >= 0:
        return int(dur // 60)
    start = _dt.datetime.fromisoformat(str(entry.get("start", "")).replace("Z", "+00:00"))
    stop_raw = entry.get("stop")
    stop = (_dt.datetime.fromisoformat(str(stop_raw).replace("Z", "+00:00"))
            if stop_raw else (now or _dt.datetime.now(TZ)))
    return max(0, int((stop - start).total_seconds() // 60))


def credit_entry(entry: dict, *, now: _dt.datetime | None = None) -> list[str]:
    """Credit each explicit value tag on a just-stopped entry. Returns the
    human lines to print (empty when nothing was credited)."""
    if not entry:
        return []
    tags = [t for t in (entry.get("tags") or []) if t in VALUE_TAGS]
    if not tags:
        return []
    desc = entry.get("description") or ""
    auto = _auto_tags_for(desc)
    if auto is None:
        return []
    now = now or _dt.datetime.now(TZ)
    mins = entry_minutes(entry, now)
    if mins <= 0:
        return []
    try:
        start = _dt.datetime.fromisoformat(str(entry["start"]).replace("Z", "+00:00")).astimezone(TZ)
    except (KeyError, ValueError):
        return []
    day = start.date()
    st = _load_state(now.date())
    out = []
    for tag in tags:
        if tag in auto:
            continue  # shortcode-implied, already earns its own points
        key = f"{entry.get('id')}:{tag}"
        if key in st["credited"]:
            continue
        try:
            _append("0n", _tag_col(tag), date=f"{day.month}/{day.day}",
                    value=f"+{mins}", src=f"tag-credit {desc}")
        except Exception as e:  # noqa: BLE001
            print(f"WARN tag_credits: #{tag} +{mins}m not written ({e})", file=sys.stderr)
            continue
        st["credited"].append(key)
        st["pending"] = [p for p in st["pending"] if p.get("key") != key]
        out.append(f"#{tag} +{mins}m → 0n")
    if out:
        _save_state(st)
    return out
