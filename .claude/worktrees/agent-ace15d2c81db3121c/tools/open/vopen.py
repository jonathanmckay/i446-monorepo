#!/usr/bin/env python3
"""Fast vault file resolver — Spotlight (mdfind) over ~/vault, ranked.

Replaces slow `find ~ -iname ...` whole-home walks (the ~/vault tree is 150k
files, 70% of them .stversions junk). mdfind hits the live Spotlight index in
~0.4s and searches filenames AND content.

Usage:
  vopen.py <query...>            # ranked candidates, filename matches first
  vopen.py --names <query...>    # filename matches only (tightest)

Output: one candidate per line, `TAG\\tPATH`, where TAG is FILE (filename match)
or TEXT (content match). The caller picks the best by relevance + canonical
location, then opens it with the right app (pdf->Preview, md->Obsidian,
html/url->Chrome).
"""
import subprocess, sys, os

VAULT = os.path.expanduser("~/vault")
JUNK = ("/.stversions/", "/ai-transcripts/", "/dream-runs/", "/.trash/",
        "/z_old/", "/readwise/", "/.git/", "/node_modules/")


def md(expr):
    try:
        out = subprocess.run(["mdfind", "-onlyin", VAULT, expr],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        out = ""
    return [l for l in out.splitlines() if l and "/.stversions/" not in l]


def main():
    args = sys.argv[1:]
    names_only = False
    if args and args[0] == "--names":
        names_only, args = True, args[1:]
    q = " ".join(args).strip()
    if not q:
        print("usage: vopen.py [--names] <query>", file=sys.stderr)
        sys.exit(1)

    fname = md(f"kMDItemFSName == '*{q}*'cd")        # filename contains query
    anym = [] if names_only else md(q)               # filename OR content

    seen, ranked = set(), []
    for p in fname:
        if p not in seen:
            seen.add(p); ranked.append(("FILE", p))
    for p in anym:
        if p not in seen:
            seen.add(p); ranked.append(("TEXT", p))

    # filename matches first; within each, deprioritize derived/junk locations
    ranked.sort(key=lambda t: (t[0] != "FILE", any(j in t[1].lower() for j in JUNK)))

    if not ranked:
        print("(no matches)")
        return
    for tag, p in ranked[:20]:
        print(f"{tag}\t{p}")


if __name__ == "__main__":
    main()
