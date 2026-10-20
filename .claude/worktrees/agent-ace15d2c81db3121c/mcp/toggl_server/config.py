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
