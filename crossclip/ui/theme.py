"""Material You theme construction for the CrossClip window."""
from __future__ import annotations

import flet as ft

_MODES = {
    "light": ft.ThemeMode.LIGHT,
    "dark": ft.ThemeMode.DARK,
    "system": ft.ThemeMode.SYSTEM,
}


def mode_from_string(value: str) -> ft.ThemeMode:
    return _MODES.get(value, ft.ThemeMode.SYSTEM)


def build_themes(seed_color: str) -> tuple[ft.Theme, ft.Theme]:
    """Light and dark Material You themes generated from one seed color."""
    common = dict(
        color_scheme_seed=seed_color,
        use_material3=True,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )
    light_theme = ft.Theme(**common)
    dark_theme = ft.Theme(**common)
    return light_theme, dark_theme
