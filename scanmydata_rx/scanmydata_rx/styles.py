"""Color palette and style constants for ScanmyData."""

# Light mode colors
LIGHT_BG = "#f3f4f6"
LIGHT_CARD = "#ffffff"
LIGHT_TEXT = "#1f2937"
LIGHT_TEXT_SECONDARY = "#6b7280"
LIGHT_BORDER = "#e5e7eb"

# Dark mode colors
DARK_BG = "#111827"
DARK_CARD = "#1f2937"
DARK_TEXT = "#f9fafb"
DARK_TEXT_SECONDARY = "#e5e7eb"
DARK_BORDER = "#6b7280"

# Brand / action colors
SKY_600 = "#0284c7"
SKY_700 = "#0369a1"
EMERALD_600 = "#059669"
RED_LIGHT_BG = "#fee2e2"
RED_DARK_TEXT = "#7f1d1d"
SUCCESS_BG = "#ecfdf5"
SUCCESS_TEXT = "#065f46"
INFO_BG = "#e0f2fe"
INFO_TEXT = "#075985"
WARNING_BG = "#fef3c7"
WARNING_TEXT = "#92400e"

# Common style dicts (light / dark mode-aware via color_mode_cond in components)
BASE_CARD_STYLE = {
    "border_radius": "12px",
    "padding": "24px",
    "box_shadow": "0 1px 3px rgba(0,0,0,0.07)",
}

INPUT_STYLE = {
    "border_radius": "8px",
    "border": f"1px solid {LIGHT_BORDER}",
    "padding": "8px 12px",
    "font_size": "14px",
    "width": "100%",
}

BUTTON_PRIMARY = {
    "background_color": SKY_600,
    "color": "white",
    "border_radius": "8px",
    "padding": "8px 16px",
    "font_weight": "600",
    "cursor": "pointer",
}

BUTTON_SECONDARY = {
    "background_color": "transparent",
    "border": f"1px solid {LIGHT_BORDER}",
    "border_radius": "8px",
    "padding": "8px 16px",
    "cursor": "pointer",
}
