import os

NEON_EXCEL_PATH = os.environ.get(
    "NEON_EXCEL_PATH",
    os.path.expanduser("~/Library/CloudStorage/OneDrive-Personal/Neon分v11.xlsx"),
)


def validate_path():
    if not os.path.isfile(NEON_EXCEL_PATH):
        raise FileNotFoundError(
            f"Neon Excel file not found at: {NEON_EXCEL_PATH}\n"
            "Set NEON_EXCEL_PATH environment variable to the correct path."
        )
    return NEON_EXCEL_PATH
