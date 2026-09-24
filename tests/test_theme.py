"""Header and appearance selector must share one persistent theme controller."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from nicegui import ui

ROOT = Path(__file__).parents[1]


class ThemeTests(unittest.TestCase):
    def test_header_and_settings_stay_synchronized(self):
        common = ast.parse((ROOT / "utils/common.py").read_text())
        page = next(n for n in common.body if isinstance(n, ast.FunctionDef) and n.name == "page_init")
        create_dark = next(n for n in page.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "dark" for t in n.targets))
        cycle = next(n for n in page.body if isinstance(n, ast.FunctionDef) and n.name == "cycle_dark")
        icon = next(n for n in page.body if isinstance(n, ast.FunctionDef) and n.name == "_dark_icon")
        user = ast.parse((ROOT / "pages/user.py").read_text())
        selector = next(n for n in ast.walk(user) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "bind_value" and "appearance_dark" in ast.unparse(n))
        # No second theme controller may be constructed on this page.
        self.assertNotIn("ui.dark_mode(", (ROOT / "pages/user.py").read_text())
        storage = {"dark_mode": None}
        scope = {"ui": ui, "app": SimpleNamespace(storage=SimpleNamespace(user=storage))}
        with ui.column() as host:
            exec(compile(ast.Module(body=[create_dark, icon, cycle], type_ignores=[]), "common", "exec"), scope)
            scope["appearance_dark"] = scope["dark"]
            button = ui.button().bind_icon_from(scope["dark"], "value", backward=scope["_dark_icon"])
            # Capture the actual settings toggle expression for interaction.
            scope["selector"] = eval(compile(ast.Expression(selector.value), "user", "eval"), scope)
            for theme, label, icon_name in [(True,"dark","dark_mode"),(False,"light","light_mode"),(None,"auto","brightness_auto")]:
                scope["cycle_dark"](button)
                self.assertIs(scope["dark"].value, theme)
                self.assertIs(storage["dark_mode"], theme)
                self.assertEqual(scope["selector"].value, label)
                self.assertEqual(button.icon, icon_name)
            scope["selector"].set_value("dark")
            self.assertIs(scope["dark"].value, True)
            self.assertIs(storage["dark_mode"], True)
            self.assertEqual(button.icon, "dark_mode")
        host.delete()
