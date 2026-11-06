import os

TOGGL_API_KEY = os.environ.get("TOGGL_API_KEY", "")
TOGGL_WORKSPACE_ID = int(os.environ.get("TOGGL_WORKSPACE_ID", "2092616"))
TIMEZONE = "America/Los_Angeles"

# Domain code -> Toggl project ID
PROJECT_MAP = {
    "g245": 108537163,
    "h335": 153212856,
    "hcb": 154064792,
    "hcm": 108359995,
    "hcmc": 109932707,
    "hci": 109216950,
    "i447": 158134455,
    "i9": 209635316,
    "m5x2": 108359987,
    "m828": 112310620,
    "qz12": 152057340,
    "s897": 109719141,
    "xk87": 163129781,
    "xk88": 108433670,
    "家": 108547409,
    "睡觉": 108358083,
    "epcn": 150114323,
    "infra": 120844877,
    "n156": 108357451,
    "q5n7": 174372636,
    "i444": 185952786,
    "h5c7": 160959920,
    "f8": 45122191,
    "hcbp": 108360024,
    "hcmc2": 108359992,
}

# Reverse map: project ID -> code (for display)
PROJECT_NAMES = {v: k for k, v in PROJECT_MAP.items()}

# Sub-projects (JM 2026-11-05): codes with no Toggl project of their own that
# file under a parent and ride along as a tag. "@xk20" = Toggl project xk87 +
# tag xk20; did-fast then credits the minutes to the kid's own 0n column
# (xk20 Theo / xk22 Ren / xk26 Rori) instead of the general xk87 0分!X.
SUBPROJECTS = {"xk20": "xk87", "xk22": "xk87", "xk26": "xk87"}


def split_subproject(code):
    """(Toggl project code, tag or None): "xk20" -> ("xk87", "xk20"); any
    other code passes through untouched with no tag."""
    c = (code or "").lower().strip()
    if c in SUBPROJECTS:
        return SUBPROJECTS[c], c
    return code, None


def with_subproject_tag(code, tags):
    """Tags list with the sub-project's tag added (once) when `code` is one."""
    _, tag = split_subproject(code)
    tags = list(tags or [])
    if tag and tag not in tags:
        tags.append(tag)
    return tags or None
