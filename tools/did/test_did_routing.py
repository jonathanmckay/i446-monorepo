#!/usr/bin/env python3
"""
Regression test harness for /did Step 0 routing logic.

Pure unit tests — no Excel, Todoist, vault mutation. Encodes the algorithms
documented in ~/.claude/skills/did/SKILL.md (Step 0 routing + Step 3 Todoist
matching, with dash normalization and alias expansion).

Run: python3 test_did_routing.py
Exit 0 if all pass, 1 otherwise.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

# Import the mark-completed helper (hyphenated filename → load via importlib).
_HERE = Path(__file__).parent
_MC_SPEC = importlib.util.spec_from_file_location(
    "mark_completed", _HERE / "mark-completed.py"
)
mc = importlib.util.module_from_spec(_MC_SPEC)
_MC_SPEC.loader.exec_module(mc)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Algorithm under test
# ---------------------------------------------------------------------------

STOPWORDS = {"the", "a", "an", "to", "with", "and", "of"}

ALIASES = {
    "math": "问学",
    "skin2skin": "问学",
    "stats m5x2": "stats m5x2",  # identity
}

# Annotation patterns: [N], (N), {N} — N may be digits or simple content
ANNOT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
PUNCT_RE = re.compile(r"[^\w\s一-鿿]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase → strip annotations → strip punctuation → split → drop stopwords.

    Per SKILL.md: "tokenize both sides (lowercase, strip [N]/(N)/stopwords,
    strip apostrophes and punctuation so mother's→mothers, don't→dont)".
    """
    text = text.lower()
    text = ANNOT_RE.sub(" ", text)
    # Strip apostrophes first so don't → dont (not do nt)
    text = text.replace("'", "").replace("’", "")
    text = PUNCT_RE.sub(" ", text)
    tokens = [t for t in text.split() if t and t not in STOPWORDS]
    return tokens


def dash_normalize(s: str) -> str:
    """Strip ` - ` sequences — SKILL.md Step 3 dash-normalization."""
    return s.replace(" - ", " ")


def parse_date(text: str) -> tuple[str, Optional[str]]:
    """If the last whitespace token is `yesterday` or `M/D`, strip and return.

    Returns (remaining_text, target_date_or_None). target_date is the raw token
    as-written (`yesterday` or `M/D`).
    """
    parts = text.rstrip().split()
    if not parts:
        return text, None
    last = parts[-1]
    if last == "yesterday" or re.fullmatch(r"\d{1,2}/\d{1,2}", last):
        return " ".join(parts[:-1]), last
    return text, None


def overlap_ratio(query_tokens: list[str], task_tokens: list[str]) -> float:
    """Query tokens found in task / total query tokens."""
    if not query_tokens:
        return 0.0
    task_set = set(task_tokens)
    found = sum(1 for t in query_tokens if t in task_set)
    return found / len(query_tokens)


def expand_alias(query: str) -> list[str]:
    """Return the set of queries to try: the original and any alias target."""
    q = query.strip()
    queries = [q]
    if q in ALIASES and ALIASES[q] != q:
        queries.append(ALIASES[q])
    return queries


def match_todoist(
    query: str,
    candidates: list[str],
    *,
    threshold_multi: float = 0.6,
    threshold_single: float = 0.4,
) -> Optional[str]:
    """Step 0.3 / Step 3 matching.

    For each expansion of the query (original + alias), compute overlap ratio
    against each candidate (after dash-normalization on both sides). The best
    candidate across all expansions wins, provided it clears the threshold:

      - ≥0.6 always qualifies
      - ≥0.4 qualifies only if there is exactly one candidate in the pool

    Returns the matched candidate string, or None.
    """
    queries = expand_alias(query)
    best: tuple[float, Optional[str]] = (0.0, None)

    for q in queries:
        q_norm = dash_normalize(q)
        q_tokens = tokenize(q_norm)
        if not q_tokens:
            continue
        for cand in candidates:
            c_norm = dash_normalize(cand)
            c_tokens = tokenize(c_norm)
            ratio = overlap_ratio(q_tokens, c_tokens)
            if ratio > best[0]:
                best = (ratio, cand)

    ratio, winner = best
    if winner is None:
        return None
    if ratio >= threshold_multi:
        return winner
    if ratio >= threshold_single and len(candidates) == 1:
        return winner
    return None


# ---------------------------------------------------------------------------
# Step 0 routing driver
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    step: str  # "1-4", "6b_posthoc", "1n", "5", "6"
    column: Optional[str] = None  # e.g. "0g" when the 0₦ header matches
    target_date: Optional[str] = None
    matched_task: Optional[str] = None
    alias_resolved: Optional[str] = None


@dataclass
class RouteInputs:
    """Fixtures for routing — kept minimal."""
    zero_neon_headers: list[str] = field(default_factory=list)   # 0n row 1
    one_neon_headers: list[str] = field(default_factory=list)    # 1n+ row 1
    todoist_tasks: list[str] = field(default_factory=list)


def route(user_input: str, fx: RouteInputs, today: str = "4/21") -> RouteResult:
    """Execute Step -2 → Step 0 for a single item, per SKILL.md."""
    text, target_date = parse_date(user_input)

    # The "past date" predicate: any explicit M/D other than today, or "yesterday".
    if target_date is None:
        past_date = False
    elif target_date == "yesterday":
        past_date = True
    else:
        past_date = target_date != today

    text = text.strip()

    # Strip trailing numeric "time" token for 0₦ matching (e.g. "0g 2" → header "0g")
    # The SKILL.md says the full input should be matched against headers. In practice,
    # /did <habit> <time> is the syntax — the first token is the header, the rest is time.
    # We take "full input minus trailing numeric token(s)" as the header candidate.
    header_candidate = text
    m = re.match(r"^(.*?)(?:\s+\d+(?:\.\d+)?)?$", text)
    if m:
        header_candidate = m.group(1).strip() or text

    # Alias resolution for the header candidate
    alias_target = ALIASES.get(header_candidate.lower())

    # Step 0.1 — 0₦ match (case-insensitive exact header match)
    for h in fx.zero_neon_headers:
        if header_candidate.lower() == h.lower():
            if past_date:
                return RouteResult(step="6b_posthoc", column=h, target_date=target_date,
                                   alias_resolved=alias_target)
            return RouteResult(step="1-4", column=h, target_date=target_date,
                               alias_resolved=alias_target)

    # Also try the alias target against 0₦ headers
    if alias_target:
        for h in fx.zero_neon_headers:
            if alias_target.lower() == h.lower():
                if past_date:
                    return RouteResult(step="6b_posthoc", column=h, target_date=target_date,
                                       alias_resolved=alias_target)
                return RouteResult(step="1-4", column=h, target_date=target_date,
                                   alias_resolved=alias_target)

    # Step 0.2 — 1n+ match (full input, case-insensitive)
    for h in fx.one_neon_headers:
        if text.lower() == h.lower():
            return RouteResult(step="1n", column=h, target_date=target_date,
                               alias_resolved=alias_target)

    # Step 0.3 — Todoist word-overlap match
    matched = match_todoist(text, fx.todoist_tasks)
    if matched is not None:
        return RouteResult(step="5", matched_task=matched, target_date=target_date,
                           alias_resolved=alias_target)

    # Step 0.4 — fall through
    return RouteResult(step="6", target_date=target_date, alias_resolved=alias_target)


# ---------------------------------------------------------------------------
# Tests — encoded from SKILL.md regression table
# ---------------------------------------------------------------------------

class TokenizeTests(unittest.TestCase):
    def test_lowercase_and_strip_annotations(self):
        self.assertEqual(tokenize("PTC feedback [180]"), ["ptc", "feedback"])

    def test_drop_stopwords(self):
        self.assertEqual(tokenize("30m session with lx"), ["30m", "session", "lx"])

    def test_strip_parens_and_braces(self):
        self.assertEqual(tokenize("m5x2 stats (4) [8]"), ["m5x2", "stats"])

    def test_apostrophes_collapse(self):
        self.assertEqual(tokenize("mother's day"), ["mothers", "day"])
        self.assertEqual(tokenize("don't quit"), ["dont", "quit"])


class DashNormTests(unittest.TestCase):
    def test_strip_dash_surrounded_by_spaces(self):
        # Exact replacement: ` - ` (space-dash-space) → single space
        self.assertEqual(dash_normalize("ibx - s897"), "ibx s897")
        # What actually matters: tokens are equivalent with/without the dash
        self.assertEqual(
            tokenize(dash_normalize("ibx - s897")),
            tokenize(dash_normalize("ibx s897")),
        )

    def test_leaves_hyphens_alone(self):
        # Per SKILL.md, only ` - ` (space-dash-space) is stripped; in-word hyphens
        # survive dash-normalization. They do get stripped by tokenize()'s punct
        # pass, which is fine for matching purposes.
        self.assertEqual(dash_normalize("multi-word"), "multi-word")


class DateParseTests(unittest.TestCase):
    def test_yesterday(self):
        self.assertEqual(parse_date("0l 2 yesterday"), ("0l 2", "yesterday"))

    def test_md_date(self):
        self.assertEqual(parse_date("0l 2 4/1"), ("0l 2", "4/1"))

    def test_no_date(self):
        self.assertEqual(parse_date("0g 2"), ("0g 2", None))

    def test_non_date_last_token_untouched(self):
        self.assertEqual(parse_date("stats m5x2"), ("stats m5x2", None))


class AliasTests(unittest.TestCase):
    def test_math_to_问学(self):
        self.assertEqual(expand_alias("math"), ["math", "问学"])

    def test_skin2skin_to_问学(self):
        self.assertEqual(expand_alias("skin2skin"), ["skin2skin", "问学"])

    def test_stats_m5x2_identity(self):
        # Identity alias — expansion list should dedupe
        self.assertEqual(expand_alias("stats m5x2"), ["stats m5x2"])

    def test_unknown_passthrough(self):
        self.assertEqual(expand_alias("some random habit"), ["some random habit"])


class OverlapTests(unittest.TestCase):
    def test_full_overlap(self):
        self.assertEqual(overlap_ratio(["a", "b"], ["a", "b", "c"]), 1.0)

    def test_half_overlap(self):
        self.assertEqual(overlap_ratio(["a", "b"], ["a", "c"]), 0.5)

    def test_word_order_indifference(self):
        # "stats m5x2" vs task tokens ["m5x2", "stats"] → 1.0
        q = tokenize("stats m5x2")
        t = tokenize("m5x2 stats (4) [8]")
        self.assertEqual(overlap_ratio(q, t), 1.0)


class RegressionTableTests(unittest.TestCase):
    """Mirror the table at the bottom of SKILL.md."""

    def test_0g_2_routes_to_0neon(self):
        fx = RouteInputs(zero_neon_headers=["0g", "0l", "hiit", "ص"])
        r = route("0g 2", fx)
        self.assertEqual(r.step, "1-4")
        self.assertEqual(r.column, "0g")

    def test_0l_2_past_date_routes_to_6b_posthoc(self):
        fx = RouteInputs(zero_neon_headers=["0g", "0l"])
        r = route("0l 2 4/1", fx, today="4/21")
        self.assertEqual(r.step, "6b_posthoc")
        self.assertEqual(r.target_date, "4/1")

    def test_ibx_dash_s897_matches(self):
        m = match_todoist("ibx - s897", ["ibx s897 [6]"])
        self.assertEqual(m, "ibx s897 [6]")

    def test_ibx_i9_matches_dashed_task(self):
        m = match_todoist("ibx i9", ["ibx - i9 [20]"])
        self.assertEqual(m, "ibx - i9 [20]")

    def test_30m_session_with_lx_matches_30m_lx_session(self):
        m = match_todoist("30m session with lx", ["30m lx session [30]"])
        self.assertEqual(m, "30m lx session [30]")

    def test_stats_m5x2_matches_despite_word_order(self):
        m = match_todoist("stats m5x2", ["m5x2 stats (4) [8]"])
        self.assertEqual(m, "m5x2 stats (4) [8]")

    def test_math_alias_matches_math_with_kids(self):
        # Alias expansion: query "math" resolves to "问学" but we MUST also try "math".
        # Todoist task has "math" in it, alias target "问学" does not.
        m = match_todoist("math", ["math with kids (60) [70]"])
        self.assertEqual(m, "math with kids (60) [70]")

    def test_hiit_completed_today_filter_documented(self):
        # Per SKILL.md: completed-today filters hiit from next suggestions.
        # This test documents the expectation — actual filtering is handled by
        # the next-task script, not the Step 0 router. We assert the contract:
        # once "hiit" is in completed-today, it should not appear in next-up.
        completed_today = {"hiit"}
        next_candidates = ["hiit", "0l", "ص"]
        filtered = [t for t in next_candidates if t not in completed_today]
        self.assertNotIn("hiit", filtered)
        self.assertEqual(filtered, ["0l", "ص"])


class Step6DuplicateGuardTests(unittest.TestCase):
    """Regression: `/did` Step 6 must detect same-day posthoc duplicates.

    Bug filed 2026-04-22. Without this guard, running the same variable-task
    /did twice in one day creates two posthoc Todoist tasks (Todoist's open-task
    search can't see the first one after it's closed). Example from
    06-posthoc-inventory.md: `talk with richard [20]` on 2026-04-13, two entries
    40 minutes apart.

    The fix: Step 6.0 pre-check via `mark_completed.is_duplicate_today` against
    a `_dup_key`-normalized lookup of `completed-today.json`.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "completed-today.json"
        self.today = date.today().isoformat()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _simulate_step6(self, content: str) -> str:
        """Simulate the Step 6 pipeline for a single posthoc content.

        Mirrors the skill contract:
          6.0. If `is_duplicate_today(content)` returns a match → skip, return "skipped".
          6.1-6.3. Else would write to 0分 + create posthoc Todoist task (no-op here).
          6.4. Record in completed-today via `append_names`.
        Returns "created" or "skipped" so the test can assert the outcome.
        """
        hit = mc.is_duplicate_today(content, today=self.today, path=self.path)
        if hit is not None:
            return "skipped"
        # Real Step 6 would do: append to 0分, create+close Todoist posthoc.
        # We only care about the guard behavior, so skip those side effects
        # and just record the content so the next call sees it.
        mc.append_names([content], today=self.today, path=self.path)
        return "created"

    def test_second_call_same_day_is_noop(self) -> None:
        """The canonical bug: two /did calls with identical content in one day."""
        content = "talk with richard [20]"
        first = self._simulate_step6(content)
        second = self._simulate_step6(content)
        self.assertEqual(first, "created")
        self.assertEqual(second, "skipped", "Second posthoc must be skipped")

        # Only one entry recorded, not two.
        data = json.loads(self.path.read_text())
        self.assertEqual(len(data["names"]), 1)

    def test_duplicate_detection_ignores_annotations(self) -> None:
        """`talk with richard [20]` must match prior `talk with richard`.

        /did Step 6 content may or may not carry the `[N]` points annotation
        depending on whether the user typed it. The dup key must strip
        annotations so both forms collide.
        """
        mc.append_names(["talk with richard"], today=self.today, path=self.path)
        hit = mc.is_duplicate_today(
            "talk with richard [20]", today=self.today, path=self.path
        )
        self.assertIsNotNone(hit)

        # And the inverse: bare content should hit a stored annotated entry.
        path2 = self.path.with_name("c2.json")
        mc.append_names(["talk with richard [20]"], today=self.today, path=path2)
        hit2 = mc.is_duplicate_today(
            "talk with richard", today=self.today, path=path2
        )
        self.assertIsNotNone(hit2)

    def test_different_content_is_not_a_duplicate(self) -> None:
        """Distinct posthocs on the same day must both go through."""
        self._simulate_step6("talk with richard [20]")
        second = self._simulate_step6("talk with sam [10]")
        self.assertEqual(second, "created")

    def test_different_day_is_not_a_duplicate(self) -> None:
        """Yesterday's posthoc doesn't block today's."""
        # Seed the file with yesterday's entry under a stale date.
        self.path.write_text(json.dumps({
            "date": "1999-01-01",
            "names": ["talk with richard"],
        }))
        hit = mc.is_duplicate_today(
            "talk with richard", today=self.today, path=self.path
        )
        self.assertIsNone(hit, "Stale-date entries must not cause false dup")

    def test_case_and_whitespace_insensitive(self) -> None:
        """Duplicate detection ignores case and whitespace noise."""
        mc.append_names(["Talk With Richard"], today=self.today, path=self.path)
        hit = mc.is_duplicate_today(
            "  talk   with   richard  ", today=self.today, path=self.path
        )
        self.assertIsNotNone(hit)

    def test_check_cli_exit_codes(self) -> None:
        """`mark-completed.py --check` must exit 0 on dup, 1 on fresh."""
        import subprocess

        # Seed a fresh file in tmp, then override HOME so the CLI targets it.
        # Easier: we test the Python function directly (above), and for CLI we
        # just confirm argparse routing — use a monkey-patched COMPLETED path.
        saved = mc.COMPLETED
        try:
            mc.COMPLETED = self.path
            # Fresh → no-dup, main() returns 1
            rc = mc.main(["mark-completed.py", "--check", "fresh content"])
            self.assertEqual(rc, 1)
            # Record it
            mc.main(["mark-completed.py", "fresh content"])
            # Now dup → main() returns 0
            rc = mc.main(["mark-completed.py", "--check", "fresh content [99]"])
            self.assertEqual(rc, 0)
        finally:
            mc.COMPLETED = saved


class ThresholdTests(unittest.TestCase):
    def test_below_single_threshold_no_match(self):
        # One token out of three matches — ratio 0.333 < 0.4
        m = match_todoist("alpha beta gamma", ["alpha zulu yankee"])
        self.assertIsNone(m)

    def test_0_4_with_single_candidate_matches(self):
        # 2/5 = 0.4 exactly, single candidate → should match
        m = match_todoist(
            "alpha beta gamma delta epsilon",
            ["alpha beta zulu yankee xray"],
        )
        self.assertEqual(m, "alpha beta zulu yankee xray")

    def test_0_4_with_multiple_candidates_no_match(self):
        # 2/5 = 0.4 — multiple candidates → must reach 0.6
        m = match_todoist(
            "alpha beta gamma delta epsilon",
            ["alpha beta zulu yankee xray", "completely unrelated task"],
        )
        self.assertIsNone(m)


# ---------------------------------------------------------------------------
# AppleScript formula generation tests
# ---------------------------------------------------------------------------

_DID_FAST = Path(__file__).parent / "did-fast.py"


class FormulaAppendTests(unittest.TestCase):
    """Regression: when a 0分 cell contains a literal number (e.g. '120')
    instead of a formula (e.g. '=0+30+90'), the append logic produced
    '120+15' which Excel treats as text, silently losing points.
    Fix: prepend '=' when oldFormula doesn't start with '='."""

    def setUp(self):
        self.src = _DID_FAST.read_text()

    def _extract_func(self, name):
        """Extract function body from source."""
        start = self.src.index(f"def {name}(")
        # Find next def at same indent level
        rest = self.src[start:]
        lines = rest.split("\n")
        end = len(lines)
        for i, line in enumerate(lines[1:], 1):
            if line.startswith("def ") or (line.startswith("class ") and not line.startswith("    ")):
                end = i
                break
        return "\n".join(lines[:end])

    def test_0fen_script_handles_literal_cell(self):
        func = self._extract_func("build_0fen_script")
        self.assertIn('character 1 of oldFormula is not "="', func,
                       "build_0fen_script must handle literal cell values (no = prefix)")
        self.assertIn('"=" & oldFormula', func,
                       "must prepend = when old value is a literal number")

    def test_1n_0fen_script_handles_literal_cell(self):
        func = self._extract_func("build_1n_0fen_script")
        self.assertIn('character 1 of oldFormula is not "="', func,
                       "build_1n_0fen_script must handle literal cell values (no = prefix)")
        self.assertIn('"=" & oldFormula', func,
                       "must prepend = when old value is a literal number")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TokenizeTests,
        DashNormTests,
        DateParseTests,
        AliasTests,
        OverlapTests,
        RegressionTableTests,
        Step6DuplicateGuardTests,
        ThresholdTests,
        FormulaAppendTests,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print()
    print("=" * 60)
    print(f"SUMMARY: {passed}/{total} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
