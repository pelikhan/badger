import ast
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "badge/apps/factory_mindset"
SPEC = importlib.util.spec_from_file_location("factory_player", APP / "factory_player.py")
player_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(player_module)
Player = player_module.Player
DURATIONS = player_module.DURATIONS
TRANSITION_MS = player_module.TRANSITION_MS
SPEC = importlib.util.spec_from_file_location("factory_slides", APP / "factory_slides.py")
slides_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(slides_module)


def advance(player, milliseconds, frame=20):
    while milliseconds:
        step = min(frame, milliseconds)
        player.advance(step)
        milliseconds -= step


class PlaybackTests(unittest.TestCase):
    def test_autoplay_complete_loop(self):
        player = Player()
        for index, duration in enumerate(DURATIONS):
            self.assertEqual(player.scene, index)
            advance(player, duration)
            self.assertEqual(player.scene, (index + 1) % len(DURATIONS))
            self.assertEqual(player.elapsed, 0)
            advance(player, TRANSITION_MS)
            self.assertEqual(player.transition, TRANSITION_MS)
        self.assertEqual(player.scene, 0)

    def test_frame_rate_independent_clock_and_transition(self):
        for milliseconds in (1230, 5400, 5550, 5830, 12340, 34000):
            players = [Player(), Player(), Player()]
            for player, frame in zip(players, (10, 25, 100)):
                advance(player, milliseconds, frame)
            states = [(p.scene, p.elapsed, p.transition) for p in players]
            self.assertEqual(states[0], states[1])
            self.assertEqual(states[0], states[2])

    def test_pause_freezes_reveals_and_slide(self):
        for transition in (0, TRANSITION_MS):
            player = Player()
            player.elapsed = 850
            player.transition = transition
            player.advance(20, toggle=True)
            advance(player, 10000)
            self.assertEqual((player.elapsed, player.transition), (850, transition))
            player.advance(20, toggle=True)
            advance(player, 100)
            self.assertEqual(player.elapsed, 950 if transition else 850)

    def test_navigation_wraps_and_resets_reading_interval(self):
        player = Player()
        player.advance(20, left=True)
        self.assertEqual((player.scene, player.elapsed, player.direction),
                         (len(DURATIONS) - 1, 0, -1))
        player.advance(20, right=True)
        self.assertEqual((player.scene, player.elapsed, player.direction), (0, 0, 1))

    def test_paused_navigation_reveals_whole_scene_and_preserves_pause(self):
        player = Player()
        player.advance(20, toggle=True)
        player.advance(20, right=True)
        self.assertTrue(player.paused)
        self.assertTrue(player.reveal_all)
        self.assertEqual((player.elapsed, player.transition), (0, TRANSITION_MS))
        player.advance(20, toggle=True)
        advance(player, DURATIONS[1] - 20)
        self.assertEqual(player.scene, 1)
        advance(player, 20)
        self.assertEqual(player.scene, 2)

    def test_conflicting_inputs_do_not_navigate(self):
        player = Player()
        player.advance(20, left=True, right=True)
        self.assertEqual(player.scene, 0)

    def test_pause_and_navigation_in_same_frame(self):
        player = Player()
        player.advance(20, right=True, toggle=True)
        self.assertTrue(player.paused)
        self.assertTrue(player.reveal_all)
        self.assertEqual(player.scene, 1)

    def test_stalls_do_not_skip_story(self):
        player = Player()
        player.advance(600000)
        self.assertEqual(player.elapsed, 100)
        player.advance(-200)
        self.assertEqual(player.elapsed, 100)

    def test_slide_eases_to_zero_in_both_directions(self):
        for direction in (-1, 1):
            player = Player()
            player.navigate(direction)
            self.assertEqual(player.offset(320), 320 * direction)
            advance(player, 210)
            self.assertEqual(player.offset(320), 40 * direction)
            advance(player, 210)
            self.assertEqual(player.offset(320), 0)


class ContractTests(unittest.TestCase):
    def test_source_slide_metrics_and_verified_talk_time_are_included(self):
        source = ast.parse((APP / "factory_slides.py").read_text())
        text = {node.value for node in ast.walk(source)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertTrue({
            "~23k+", "700+", "6h", "100", "LoC", "50% started", "50% AW.",
            "Stop manually", "translating intent", "into code.",
            "Engineer processes", "that create", "trusted features.",
            "Markdown", "INTENT + GUARDRAILS", "GitHub Actions", "RUN THE WORKFLOW",
            "Agents in a sandbox", "CONTROLLED ENVIRONMENT",
            "Build a human-agent", "process that works", "for you.",
            "The Factory", "Mindset",
            "WED OCT 28, 2026", "12:50-1:20 PM PDT", "MAIN STAGE", "FESTIVAL PAVILION",
        }.issubset(text))
        self.assertEqual(slides_module.TALK["title"], "The Factory Mindset")

    def test_dark_palette_matches_runtime_and_keeps_text_contrast(self):
        self.assertLess(max(slides_module.BACKGROUND), 25)

        def luminance(value):
            channels = [channel / 255 for channel in value]
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                      for v in channels]
            return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
        for name in ("INK", "GREEN", "PURPLE", "BLUE", "PINK", "MUTED"):
            self.assertGreaterEqual(
                (luminance(getattr(slides_module, name)) + 0.05)
                / (luminance(slides_module.BACKGROUND) + 0.05),
                4.5, name)

    def test_no_images_or_custom_icon_are_shipped_or_loaded(self):
        self.assertFalse(any(path.suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp")
                             for path in APP.rglob("*")))
        self.assertFalse((APP / "icon.png").exists())
        tree = ast.parse((APP / "__init__.py").read_text())
        self.assertFalse(any(isinstance(node, ast.Name) and node.id == "image"
                             for node in ast.walk(tree)))
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr == "blit"
                             for node in ast.walk(tree)))

    def test_home_is_launcher_owned_and_run_is_module_level(self):
        tree = ast.parse((APP / "__init__.py").read_text())
        self.assertFalse(any(isinstance(node, ast.Name) and node.id == "BUTTON_HOME"
                             for node in ast.walk(tree)))
        self.assertTrue(any(isinstance(node, ast.Expr)
                            and isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Name)
                            and node.value.func.id == "run" for node in tree.body))

    def test_reveals_finish_before_scene_advance(self):
        self.assertEqual(len(slides_module.SLIDES), len(DURATIONS))
        for duration, slide in zip(DURATIONS, slides_module.SLIDES):
            for text, x, y, size, rgb, delay in slide["lines"]:
                self.assertGreaterEqual(size, 12)
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(y + size, 240, text)
                self.assertLess(delay + player_module.REVEAL_MS, duration)
            for x, y, width, height, rgb, delay in slide.get("boxes", ()):
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + width, 320)
                self.assertLessEqual(y + height, 240)
                self.assertLess(delay + player_module.REVEAL_MS, duration)

    def test_native_renderer_draws_every_slide_inside_the_display(self):
        app, screen = native_app()
        for index, slide in enumerate(slides_module.SLIDES):
            app["player"].scene = index
            app["player"].reveal_all = True
            screen.lines.clear()
            app["draw"]()
            self.assertEqual(len(screen.lines), len(slide["lines"]))
            for text, x, y, size in screen.lines:
                width, height = screen.measure_text(text, font_size=size)
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + width, screen.width + 0.01, text)
                self.assertLessEqual(y + height, screen.height, text)

    def test_native_text_reveals_are_staged_not_a_static_picture(self):
        app, screen = native_app()
        counts = []
        for elapsed in (0, 800, 3000):
            app["player"].elapsed = elapsed
            screen.lines.clear()
            app["draw"]()
            counts.append(len(screen.lines))
        self.assertLess(counts[0], counts[1])
        self.assertLess(counts[1], counts[2])


def native_app():
    class Screen:
        width, height = 320, 240
        clip = (0, 0, 320, 240)

        def __init__(self):
            self.lines = []

        def measure_text(self, text, font_size):
            return len(text) * font_size * 0.55, font_size

        def text(self, text, x, y, font_size):
            self.lines.append((text, x, y, font_size))

        def rectangle(self, bounds):
            pass

        def shape(self, value):
            pass

        def line(self, *coordinates):
            pass

    screen = Screen()
    geometry = lambda *args: SimpleNamespace(stroke=lambda width: args)
    app = {
        "badge": SimpleNamespace(mode=lambda value: None, ticks_delta=20,
                                 pressed=lambda action: False),
        "screen": screen, "font": SimpleNamespace(load=lambda path: object()),
        "color": SimpleNamespace(rgb=lambda *rgb: rgb),
        "shape": SimpleNamespace(rounded_rectangle=geometry, circle=geometry),
        "HIRES": 1, "VSYNC": 2, "BUTTON_LEFT": "left", "BUTTON_RIGHT": "right",
        "BUTTON_SELECT": "select", "run": lambda callback: None,
    }
    original_path = sys.path[:]
    try:
        with patch("os.chdir"), patch.dict(sys.modules, {
            "factory_player": player_module, "factory_slides": slides_module,
        }):
            exec(compile((APP / "__init__.py").read_text(),
                         str(APP / "__init__.py"), "exec"), app)
    finally:
        sys.path[:] = original_path
    return app, screen


if __name__ == "__main__":
    unittest.main()
