"""dtd inbox badges (2026-11-12, JM: 'show up in dtd as ibx (1) if there were
one message'). ibx0 writes per-host snapshots to ~/vault/z_ibx; the list
generator badges each daily inbox habit row with its pending count, placed
ahead of the trailing (N) [N] estimates so the minutes/points column and the
done-router's name stripping are untouched. A failed or stale ping = no badge.
"""
import re
from pathlib import Path

HERE = Path(__file__).parent
DTD = (HERE / "dtd.sh").read_text()


def _listgen():
    i = DTD.index("import unicodedata as _ud")
    return DTD[i:DTD.index("# View mode (8th arg", i)]


def test_listgen_reads_badges_preferring_this_host():
    g = _listgen()
    assert "import inbox_counts as _ic" in g
    assert "_ic.read_badges(prefer_host=_ic.host_slug())" in g
    # a broken reader must never break the list
    j = g.index("import inbox_counts as _ic")
    assert "except Exception:" in g[j:j + 200] and "_ibx_badges = {}" in g[j:j + 240]


def test_badge_sits_ahead_of_the_estimates_and_only_when_ok():
    body = DTD[DTD.index("for t in unique:"):DTD.index("# Middle-truncate if needed")]
    assert "if _bd and _bd.get('ok') and not is_ritual:" in body
    assert "' \\u2709' + str(_bd['count'])" in body
    # the badge is inserted before the trailing estimate run, same as the chain link
    assert body.index("_bd = _ibx_badges.get(") < body.index("link_url = None")


def test_badge_lookup_bridges_sheet_spelling():
    body = DTD[DTD.index("for t in unique:"):DTD.index("# Middle-truncate if needed")]
    assert "clean.replace(' - ', ' ')" in body


def test_generator_stays_double_quote_free():
    # the generator is embedded in a double-quoted zsh -c string
    g = _listgen()
    seg = g[g.index("# Inbox badges (2026-11-12)"):g.index("_ibx_badges = _ic.read_badges") + 80]
    assert '"' not in seg and "`" not in seg


def test_watcher_rerenders_when_a_snapshot_changes():
    w = DTD[DTD.index('ac_reload=""'):DTD.index('cur_ac=$(stat -f %Fm')]
    assert 'inbox-counts-*.json' in w and 'ac_reload=1' in w


def test_estimate_column_tolerates_the_badge():
    # mirror rjust_est's tail regex: a '\u2709 3' before the estimates must not
    # be swallowed into the estimate column nor hide the estimates from it
    EST_TOK = r'(?:\(\(?\d+\)?\)|\[\d*G?\]|\[[0-9.+]*/m\]|\{\d+\})'
    EST_TAIL = re.compile(r'(\s*(?:' + EST_TOK + r'\s*)+)$')
    m = EST_TAIL.search("ibx i9 \u22093 (4) [20]")
    assert m and m.group(1).strip() == "(4) [20]"
