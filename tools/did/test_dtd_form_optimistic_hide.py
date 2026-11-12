#!/usr/bin/env python3
"""Regression (2026-11-12): "for tasks that require a survey (-1g, 0l) I see
the task at the top of the list flash before it disappears ... have these
tasks off the task list by the time the list appears again."

Cause: -1g / 0g / 0l save in a DETACHED child (tools/-1g/-1g-form.py,
tools/0s/0s.py detach()), so when fzf reloaded after the form the card was
still open in the cache and nothing had hidden it; the row sat at the top
(its timer had just started) until the child's did-fast refresh landed.

Fix: the form wrapper exports DTD_TASK_ID + DTD_HIDE_IDS_FILE (= $REMOVED.ids,
the same id-hide file alt-enter completions use); lib/dtd_hide.py lets the
form append the id the moment it knows the child will close the card and
strip it if the close fails. Both routers that open a form chain a
conditional `exclude` so even fzf's post-execute repaint omits the row.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()


def _heredoc(var, eof):
    lines = DTD.split("\n")
    i0 = next(i for i, l in enumerate(lines) if l.startswith(f'cat > "${var}"'))
    i1 = next(i for i in range(i0, len(lines)) if lines[i] == eof)
    return "\n".join(lines[i0 + 1:i1])


def test_form_wrapper_exports_hide_contract_before_running_the_form():
    body = _heredoc("DTD_FORM", "FORMEOF")
    assert 'export DTD_TASK_ID="\\$id"' in body
    assert 'export DTD_HIDE_IDS_FILE="$DTD_REMOVED.ids"' in body, (
        "must be the very file the list builder's removed_ids check reads")
    exports = body.index("export DTD_HIDE_IDS_FILE")
    assert exports < body.index('python3 "\\$form"'), "exports must precede the form"


def test_both_form_routes_chain_a_conditional_exclude():
    enter = _heredoc("DTD_PICKENTER", "PICKENTEREOF")
    assert ('print -rn -- "execute($DTD_FORM \\$id \\$_form)+transform('
            'grep -qxF -- \\$id $DTD_REMOVED.ids 2>/dev/null && echo exclude)"') in enter
    done = _heredoc("DTD_DONE_ROUTER", "ROUTEREOF")
    m = re.search(r"printf 'execute\(%s %s %s --detach\)\+transform\(grep -qxF -- %s %s 2>/dev/null && echo exclude\)' (.*)", done)
    assert m, "alt-enter's -1g form route must carry the same conditional exclude"
    assert m.group(1).rstrip().endswith('"\\$_id" "$DTD_REMOVED.ids"')


def test_list_builder_still_hides_by_removed_ids():
    assert "removed_file + '.ids'" in DTD and "str(t['id']) in removed_ids" in DTD


def test_helper_exists_and_forms_import_it():
    root = HERE.parent.parent
    assert (root / "lib" / "dtd_hide.py").exists()
    for rel in ("tools/-1g/-1g-form.py", "tools/0s/0s.py"):
        src = (root / rel).read_text()
        assert "import dtd_hide" in src and "dtd_hide.hide_card()" in src and "dtd_hide.unhide_card()" in src, rel
