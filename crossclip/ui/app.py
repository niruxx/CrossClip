"""The Flet (Material You) UI for CrossClip."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import flet as ft
from PIL import Image

from .. import autostart, config, utils, winfocus
from ..database import ClipItem, Database
from ..config import Settings
from ..monitor import ClipboardMonitor, delete_item_files
from ..tray import Tray
from . import theme as theme_mod


class CrossClipApp:
    def __init__(
        self,
        page: ft.Page,
        db: Database,
        settings: Settings,
        monitor: ClipboardMonitor,
        signal: utils.ChangeSignal,
        *,
        start_minimized: bool = False,
        icon_path: Optional[Path] = None,
        on_tray_ready=lambda tray: None,
    ):
        self.page = page
        self.db = db
        self.settings = settings
        self.monitor = monitor
        self.signal = signal
        self.start_minimized = start_minimized
        self._icon_path = icon_path
        self._on_tray_ready = on_tray_ready

        self.tray: Optional[Tray] = None
        self._tray_hint_shown = False
        self.current_search = ""
        # Applying window properties (title_bar_hidden in particular) right
        # after the native window is created can make the desktop runner
        # tear down and recreate it, which fires a spurious CLOSE event.
        # Ignore CLOSE until the startup sequence has actually finished so
        # that doesn't get misread as the user closing the app.
        self._startup_complete = False

        self.search_field: ft.TextField
        self.list_view: ft.ListView
        self._pause_button: ft.IconButton

    # -- lifecycle ----------------------------------------------------------

    def build(self) -> None:
        page = self.page
        page.title = config.APP_NAME
        page.theme_mode = theme_mod.mode_from_string(self.settings.theme_mode)
        page.theme, page.dark_theme = theme_mod.build_themes(self.settings.seed_color)
        page.padding = 0
        page.window.width = 440
        page.window.height = 680
        page.window.min_width = 380
        page.window.min_height = 440
        page.window.prevent_close = True
        page.window.title_bar_hidden = True  # replaced with our own Material You bar below
        if sys.platform == "darwin":
            page.window.title_bar_buttons_hidden = True  # hide the native traffic lights too
        page.window.visible = False  # revealed by _startup_animation once centered
        page.window.opacity = 0
        page.window.skip_task_bar = self.start_minimized
        page.window.on_event = self._on_window_event
        if sys.platform == "win32" and self._icon_path is not None:
            page.window.icon = str(self._icon_path)  # .ico only has effect on Windows

        title_bar = self._build_title_bar()
        header = self._build_header()
        self.list_view = ft.ListView(
            expand=True,
            spacing=10,
            padding=ft.padding.Padding(left=16, right=16, top=8, bottom=12),
            auto_scroll=False,
        )

        page.add(
            ft.Container(
                content=ft.Column(
                    [
                        title_bar,
                        header,
                        ft.Divider(height=1),
                        ft.Container(self.list_view, expand=True),
                    ],
                    expand=True,
                    spacing=0,
                ),
                expand=True,
                bgcolor=ft.Colors.SURFACE,
                border=ft.border.Border.all(1.5, ft.Colors.with_opacity(0.6, ft.Colors.PRIMARY)),
            )
        )

        self.refresh_list()
        page.run_task(self._watch_loop)
        page.run_task(self._startup_animation)

        self.tray = Tray(
            on_show=lambda: page.run_task(self.show_window),
            on_quit=lambda: page.run_task(self.quit_app),
            is_paused=lambda: self.settings.monitor_paused,
            on_toggle_pause=self._toggle_pause_plain,
            is_autostart=lambda: autostart.is_enabled(),
            on_toggle_autostart=self._toggle_autostart_plain,
            autostart_label=autostart.toggle_label(),
        )
        self.tray.start()
        self._on_tray_ready(self.tray)

    async def _startup_animation(self) -> None:
        await self.page.window.wait_until_ready_to_show()
        await self.page.window.center()
        if self.start_minimized:
            self.page.window.opacity = 1
            self.page.update()
            self._startup_complete = True
            return
        self.page.window.visible = True
        self.page.update()
        await self.page.window.to_front()
        self.page.window.focused = True
        self.page.update()
        winfocus.force_foreground(config.APP_NAME)
        await self._fade_window(0, 1)
        self._startup_complete = True

    async def _fade_window(self, start: float, end: float, duration: float = 0.22, steps: int = 12) -> None:
        window = self.page.window
        window.opacity = start
        self.page.update()
        for i in range(1, steps + 1):
            window.opacity = start + (end - start) * (i / steps)
            self.page.update()
            await asyncio.sleep(duration / steps)

    async def show_window(self) -> None:
        self.page.window.opacity = 0
        self.page.window.visible = True
        self.page.window.skip_task_bar = False
        self.page.window.minimized = False
        await self.page.window.to_front()
        self.page.window.focused = True
        self._sync_pause_icon()
        self.page.update()
        winfocus.force_foreground(config.APP_NAME)
        await self._fade_window(0, 1)

    async def quit_app(self) -> None:
        self.monitor.stop()
        if self.page.window.visible:
            await self._fade_window(1, 0)
        await self.page.window.destroy()

    async def _hide_to_tray_animated(self) -> None:
        await self._fade_window(1, 0)
        self.page.window.visible = False
        self.page.window.skip_task_bar = True
        self.page.window.opacity = 1  # reset so the next show-fade starts from a clean state
        self.page.update()
        if not self._tray_hint_shown and self.tray is not None:
            self._tray_hint_shown = True
            self.tray.notify(
                "CrossClip is still running in the tray. Click the icon to reopen it."
            )

    def _on_window_event(self, e: ft.WindowEvent) -> None:
        if e.type == ft.WindowEventType.CLOSE and self._startup_complete:
            self.page.run_task(self._hide_to_tray_animated)

    def _on_minimize_click(self, e: ft.Event) -> None:
        self.page.window.minimized = True
        self.page.update()

    def _on_titlebar_close_click(self, e: ft.Event) -> None:
        self.page.run_task(self._hide_to_tray_animated)

    def _build_title_bar(self) -> ft.Control:
        # NOTE: this Flet build has a layout bug where any control placed
        # after an `expand=True` sibling in a Row silently fails to render
        # (reproduced with plain IconButtons and plain Containers alike, with
        # or without WindowDragArea/Stack positioning). The one reliable
        # shape is "fixed-size children first, expand=True child last" - so
        # the traffic-light buttons come first (like macOS) and the brand
        # fills the remaining space after them.
        close_button = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=16,
            icon_color=ft.Colors.ON_PRIMARY_CONTAINER,
            tooltip="Close",
            on_click=self._on_titlebar_close_click,
        )
        minimize_button = ft.IconButton(
            icon=ft.Icons.REMOVE_ROUNDED,
            icon_size=16,
            icon_color=ft.Colors.ON_PRIMARY_CONTAINER,
            tooltip="Minimize",
            on_click=self._on_minimize_click,
        )
        brand = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.CONTENT_PASTE_ROUNDED, size=16, color=ft.Colors.ON_PRIMARY_CONTAINER),
                    ft.Text(
                        config.APP_NAME,
                        size=13,
                        weight=ft.FontWeight.W_600,
                        color=ft.Colors.ON_PRIMARY_CONTAINER,
                    ),
                ],
                spacing=8,
            ),
            expand=True,
            padding=ft.padding.Padding(left=10, right=0, top=0, bottom=0),
            alignment=ft.alignment.Alignment.CENTER_LEFT,
        )
        bar = ft.Container(
            content=ft.Row([close_button, minimize_button, brand], spacing=0),
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            height=42,
            padding=ft.padding.Padding(left=4, right=0, top=0, bottom=0),
        )
        return ft.WindowDragArea(content=bar, maximizable=False)

    async def _watch_loop(self) -> None:
        last_paused = self.settings.monitor_paused
        while True:
            await asyncio.sleep(0.5)
            if self.signal.consume():
                self.refresh_list()
            if self.settings.monitor_paused != last_paused:
                last_paused = self.settings.monitor_paused
                self._sync_pause_icon()
                self.page.update()

    # -- header / list --------------------------------------------------------

    def _build_header(self) -> ft.Control:
        self.search_field = ft.TextField(
            hint_text="Search history",
            prefix_icon=ft.Icons.SEARCH_ROUNDED,
            dense=True,
            filled=True,
            border_radius=20,
            expand=True,
            on_change=self._on_search_change,
        )
        self._pause_button = ft.IconButton(
            icon=ft.Icons.PAUSE_ROUNDED,
            tooltip="Pause monitoring",
            on_click=self._on_pause_click,
        )
        settings_button = ft.IconButton(
            icon=ft.Icons.SETTINGS_ROUNDED,
            tooltip="Settings",
            on_click=self._open_settings,
        )
        self._sync_pause_icon()
        return ft.Container(
            content=ft.Row([self.search_field, self._pause_button, settings_button], spacing=4),
            padding=ft.padding.Padding(left=16, right=16, top=12, bottom=12),
        )

    def _sync_pause_icon(self) -> None:
        paused = self.settings.monitor_paused
        self._pause_button.icon = ft.Icons.PLAY_ARROW_ROUNDED if paused else ft.Icons.PAUSE_ROUNDED
        self._pause_button.tooltip = "Resume monitoring" if paused else "Pause monitoring"

    def _on_pause_click(self, e: ft.Event) -> None:
        self._toggle_pause_plain()
        self._sync_pause_icon()
        self.page.update()

    def _toggle_pause_plain(self) -> None:
        self.settings.monitor_paused = not self.settings.monitor_paused
        self.settings.save()

    def _toggle_autostart_plain(self) -> None:
        new_value = not autostart.is_enabled()
        if autostart.set_enabled(new_value):
            self.settings.launch_on_boot = new_value
            self.settings.save()

    def _on_search_change(self, e: ft.Event) -> None:
        self.current_search = e.control.value or ""
        self.refresh_list(self.current_search)

    def refresh_list(self, search: Optional[str] = None) -> None:
        query = self.current_search if search is None else search
        items = self.db.list_items(query)
        if not items:
            self.list_view.controls = [self._build_empty_state(bool(query))]
        else:
            self.list_view.controls = [self._build_card(item) for item in items]
        self.page.update()

    def _build_empty_state(self, is_search: bool) -> ft.Control:
        message = "No matches" if is_search else "No clipboard history yet"
        hint = "Try a different search." if is_search else "Copy some text or an image to get started."
        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.CONTENT_PASTE_OFF_ROUNDED, size=40, color=ft.Colors.OUTLINE),
                    ft.Text(message, size=14, color=ft.Colors.OUTLINE),
                    ft.Text(hint, size=12, color=ft.Colors.OUTLINE),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
            alignment=ft.alignment.Alignment.CENTER,
            padding=40,
            expand=True,
        )

    def _build_card(self, item: ClipItem) -> ft.Control:
        is_image = item.type == "image"
        if is_image and item.thumb_path:
            media: ft.Control = ft.Container(
                content=ft.Image(src=f"images/{item.thumb_path}", fit=ft.BoxFit.COVER),
                height=160,
                border_radius=12,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            )
            meta = f"{item.width}×{item.height} image"
        else:
            media = ft.Text(
                utils.preview_line(item.content or "", 280),
                max_lines=6,
                overflow=ft.TextOverflow.ELLIPSIS,
                size=14,
            )
            meta = f"{len(item.content or '')} characters"

        header_row = ft.Row(
            [
                ft.Icon(
                    ft.Icons.IMAGE_ROUNDED if is_image else ft.Icons.TEXT_SNIPPET_ROUNDED,
                    size=16,
                    color=ft.Colors.PRIMARY,
                ),
                ft.Text(meta, size=11, color=ft.Colors.OUTLINE),
                ft.Container(expand=True),
                ft.Text(utils.human_time(item.updated_at), size=11, color=ft.Colors.OUTLINE),
            ],
            spacing=6,
        )

        actions = ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.PUSH_PIN_ROUNDED if item.pinned else ft.Icons.PUSH_PIN_OUTLINED,
                    icon_size=18,
                    tooltip="Unpin" if item.pinned else "Pin",
                    on_click=lambda e, i=item: self._on_toggle_pin(i),
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    icon_size=18,
                    tooltip="Delete",
                    on_click=lambda e, i=item: self._on_delete(i),
                ),
            ],
            spacing=0,
            alignment=ft.MainAxisAlignment.END,
        )

        body = ft.Column([header_row, media, actions], spacing=8)

        return ft.Card(
            content=ft.Container(
                content=body,
                padding=14,
                border_radius=16,
                ink=True,
                on_click=lambda e, i=item: self._on_copy(i),
            ),
        )

    # -- item actions ---------------------------------------------------------

    def _on_copy(self, item: ClipItem) -> None:
        try:
            if item.type == "text":
                self.monitor.backend.write_text(item.content or "")
            else:
                path = config.IMAGES_DIR / (item.image_path or "")
                with Image.open(path) as img:
                    img.load()
                    self.monitor.backend.write_image(img)
        except NotImplementedError:
            self._snack("Copying images isn't supported on this platform.")
            return
        except Exception:
            self._snack("Couldn't copy that item.")
            return

        self.db.add_or_bump(
            item.type,
            item.content_hash,
            content=item.content,
            image_path=item.image_path,
            thumb_path=item.thumb_path,
            width=item.width,
            height=item.height,
        )
        self.refresh_list()
        self._snack("Copied to clipboard")

    def _on_toggle_pin(self, item: ClipItem) -> None:
        self.db.toggle_pin(item.id)
        self.refresh_list()

    def _on_delete(self, item: ClipItem) -> None:
        removed = self.db.delete_item(item.id)
        if removed:
            delete_item_files(removed)
        self.refresh_list()

    def _snack(self, message: str) -> None:
        self.page.show_dialog(ft.SnackBar(ft.Text(message), duration=1600))

    # -- settings dialog --------------------------------------------------------

    def _open_settings(self, e: ft.Event = None) -> None:
        self.page.show_dialog(self._build_settings_dialog())

    def _build_settings_dialog(self) -> ft.AlertDialog:
        s = self.settings

        theme_radio = ft.RadioGroup(
            value=s.theme_mode,
            content=ft.Row(
                [
                    ft.Radio(value="system", label="System"),
                    ft.Radio(value="light", label="Light"),
                    ft.Radio(value="dark", label="Dark"),
                ]
            ),
            on_change=self._on_theme_mode_change,
        )

        color_row = ft.Row(
            [self._color_swatch(name, hex_value) for name, hex_value in config.SEED_COLOR_CHOICES.items()],
            spacing=10,
            wrap=True,
        )

        max_items_text = ft.Text(f"Keep up to {s.max_history_items} items")
        max_items_slider = ft.Slider(
            min=50,
            max=1000,
            divisions=19,
            value=s.max_history_items,
            label="{value}",
            on_change=lambda e: self._on_max_items_drag(e, max_items_text),
            on_change_end=self._on_max_items_commit,
        )

        capture_images_switch = ft.Switch(
            label="Capture images",
            value=s.capture_images,
            on_change=self._on_capture_images_change,
        )

        autostart_supported = autostart.is_supported()
        autostart_switch = ft.Switch(
            label=autostart.toggle_label(),
            value=autostart.is_enabled() if autostart_supported else False,
            disabled=not autostart_supported,
            on_change=self._on_autostart_change,
        )

        start_minimized_switch = ft.Switch(
            label="Start minimized to tray",
            value=s.start_minimized,
            on_change=self._on_start_minimized_change,
        )

        columns = [
            ft.Text("Appearance", weight=ft.FontWeight.BOLD, size=12, color=ft.Colors.OUTLINE),
            theme_radio,
            ft.Text("Accent color", size=12, color=ft.Colors.OUTLINE),
            color_row,
            ft.Divider(),
            ft.Text("History", weight=ft.FontWeight.BOLD, size=12, color=ft.Colors.OUTLINE),
            max_items_text,
            max_items_slider,
            capture_images_switch,
            ft.Row(
                [
                    ft.OutlinedButton(
                        "Clear unpinned",
                        icon=ft.Icons.DELETE_SWEEP_OUTLINED,
                        on_click=self._on_clear_history,
                    ),
                    ft.TextButton("Clear everything", on_click=self._on_clear_all),
                ]
            ),
            ft.Divider(),
            ft.Text("Startup", weight=ft.FontWeight.BOLD, size=12, color=ft.Colors.OUTLINE),
            autostart_switch,
            start_minimized_switch,
        ]
        if not autostart_supported:
            columns.append(
                ft.Text(
                    "Launch on boot isn't supported on this platform.",
                    size=11,
                    color=ft.Colors.ERROR,
                )
            )

        return ft.AlertDialog(
            modal=False,
            title=ft.Text("Settings"),
            content=ft.Container(
                width=340,
                height=440,
                content=ft.Column(columns, tight=True, spacing=10, scroll=ft.ScrollMode.AUTO),
            ),
            actions=[ft.TextButton("Close", on_click=lambda e: self.page.pop_dialog())],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def _color_swatch(self, name: str, hex_value: str) -> ft.Control:
        selected = self.settings.seed_color.lower() == hex_value.lower()
        return ft.Container(
            width=32,
            height=32,
            bgcolor=hex_value,
            border_radius=16,
            tooltip=name,
            border=ft.border.Border.all(3, ft.Colors.PRIMARY) if selected else None,
            on_click=lambda e, c=hex_value: self._on_seed_color_change(c),
        )

    def _on_theme_mode_change(self, e: ft.Event) -> None:
        self.settings.theme_mode = e.control.value
        self.settings.save()
        self.page.theme_mode = theme_mod.mode_from_string(self.settings.theme_mode)
        self.page.update()

    def _on_seed_color_change(self, hex_value: str) -> None:
        self.settings.seed_color = hex_value
        self.settings.save()
        self.page.theme, self.page.dark_theme = theme_mod.build_themes(hex_value)
        self.page.pop_dialog()
        self._open_settings()

    def _on_max_items_drag(self, e: ft.Event, label: ft.Text) -> None:
        label.value = f"Keep up to {int(e.control.value)} items"
        label.update()

    def _on_max_items_commit(self, e: ft.Event) -> None:
        value = int(e.control.value)
        self.settings.max_history_items = value
        self.settings.save()
        removed = self.db.purge_excess(value)
        for item in removed:
            delete_item_files(item)
        self.refresh_list()

    def _on_capture_images_change(self, e: ft.Event) -> None:
        self.settings.capture_images = e.control.value
        self.settings.save()

    def _on_autostart_change(self, e: ft.Event) -> None:
        desired = e.control.value
        ok = autostart.set_enabled(desired)
        if not ok:
            e.control.value = not desired
            self._snack("Couldn't update the startup setting.")
        else:
            self.settings.launch_on_boot = desired
            self.settings.save()
        self.page.update()

    def _on_start_minimized_change(self, e: ft.Event) -> None:
        self.settings.start_minimized = e.control.value
        self.settings.save()

    def _on_clear_history(self, e: ft.Event) -> None:
        removed = self.db.clear_history(keep_pinned=True)
        for item in removed:
            delete_item_files(item)
        self.refresh_list()
        self.page.pop_dialog()
        self._snack("Cleared clipboard history")

    def _on_clear_all(self, e: ft.Event) -> None:
        removed = self.db.clear_history(keep_pinned=False)
        for item in removed:
            delete_item_files(item)
        self.refresh_list()
        self.page.pop_dialog()
        self._snack("Cleared everything")
