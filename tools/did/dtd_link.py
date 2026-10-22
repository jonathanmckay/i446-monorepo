"""dtd_link: which Chrome profile a task's link opens in, and the clickable URL.

2026-10-22 ("a hyperlink that will open the link in the correct chrome
context in a separate window if I click on it either from detail or main
view"): a terminal hyperlink (OSC 8) hands its URL to macOS, which opens it in
whatever Chrome profile was used last. So the hyperlink target is not the URL
itself but hammerspoon://dtdlink?p=<profile dir>&u=<url>; the handler in
~/.hammerspoon/dtd-link.lua runs `open -na "Google Chrome" --args
--profile-directory=<p> --new-window <u>`. Shared by the list generator in
dtd.sh, edit-form.py (details screen) and dtd-open-link.py (ctrl-l).
"""
from __future__ import annotations

import re
from urllib.parse import quote

# Profile by domain label: m5x2 -> m5c7.com, i9 -> MSFT, anything else -> 个
# (same directories as ibx0's _CHROME_PROFILES). The Hammerspoon handler
# accepts only these directories.
PROFILES = {"m5x2": ("Profile 11", "m5c7.com"), "i9": ("Profile 1", "MSFT")}
DEFAULT_PROFILE = ("Profile 5", "个")

# Markdown [text](url) first, then bare URLs. Parens are allowed inside a bare
# URL only when balanced, so SharePoint-style ...(1).docx links survive while
# a URL in "(see https://x.com)" still stops at the closing paren.
MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)\)")
URL = re.compile(r"https?://(?:[^\s<>()\[\]]|\([^\s<>()\[\]]*\))+")


def profile_for(labels) -> tuple[str, str]:
    for dom, prof in PROFILES.items():
        if dom in (labels or []):
            return prof
    return DEFAULT_PROFILE


def urls_in(*texts: str) -> list[str]:
    """Every URL in the texts, in order, deduplicated, trailing punctuation off."""
    out: list[str] = []
    for t in texts:
        for u in URL.findall(t or ""):
            u = u.rstrip(".,;:!?'\"")
            if u not in out:
                out.append(u)
    return out


def click_url(url: str, labels) -> str:
    """The OSC 8 target that opens url in the labels' profile, new window."""
    return f"hammerspoon://dtdlink?p={quote(profile_for(labels)[0], safe='')}&u={quote(url, safe='')}"


def osc8(target: str, text: str) -> str:
    st = "\x1b\\"
    return f"\x1b]8;;{target}{st}{text}\x1b]8;;{st}"


def open_cmd(url: str, profile_dir: str) -> list[str]:
    return ["open", "-na", "Google Chrome", "--args", f"--profile-directory={profile_dir}",
            "--new-window", url]
