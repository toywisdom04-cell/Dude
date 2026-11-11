import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.intent_router import is_parseable_when, parse_intent


class WhenParsingTest(unittest.TestCase):
    def test_parseable_shapes(self):
        for w in ["14:30", "2:30pm", "9am", "12pm", "1:05 pm",
                  "in 20 minutes", "in 1 hour", "in 30 sec",
                  "in an hour", "in half an hour", "in a minute",
                  "tomorrow 14:30", "tomorrow at 9am", "tomorrow 9:15"]:
            with self.subTest(when=w):
                self.assertTrue(is_parseable_when(w), msg=w)

    def test_unparseable_shapes(self):
        for w in ["", "later", "next week", "every weekday at 9",
                  "in a bit", "sometime", "at the office"]:
            with self.subTest(when=w):
                self.assertFalse(is_parseable_when(w), msg=w)


class IntentRouterTest(unittest.TestCase):
    def expected(self, mapping):
        for text, want in mapping.items():
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text), want, msg=text)

    def test_create_file(self):
        self.expected({
            "create file test.txt": "create_file",
            "Create File notes.md": "create_file",
            "please create file foo.py": "create_file",
        })

    def test_open_file_explicit(self):
        self.expected({
            "open file C:/docs/report.pdf": "open_file",
            "open file notes.txt": "open_file",
        })

    def test_open_file_by_extension(self):
        self.expected({
            "open report.pdf": "open_file",
            "open data.xlsx": "open_file",
            "open script.py": "open_file",
        })

    def test_open_file_by_path_token(self):
        self.expected({
            r"open C:\docs\data.xlsx": "open_file",
            "open ~/notes.txt": "open_file",
            "open ../stuff.log": "open_file",
            "open ./config.json": "open_file",
        })

    def test_open_app(self):
        self.expected({
            "open notepad": "open_app",
            "open chrome": "open_app",
            "open the calculator": "open_app",
            "please open the vscode": "open_app",
            "open word": "open_app",
            "open excel": "open_app",
            "open spotify": "open_app",
            "open discord": "open_app",
            "open vlc": "open_app",
            "open the control panel": "open_app",
            "open task manager": "open_app",
            "open command prompt": "open_app",
        })

    def test_close_app(self):
        self.expected({
            "close chrome": "close_app",
            "close the notepad": "close_app",
            "close excel": "close_app",
            "close spotify": "close_app",
        })

    def test_bare_app_fallback(self):
        # Any single-word app resolvable on PATH routes to app control.
        self.expected({
            "open python": "open_app",
            "open git bash": "open_app",
        })
        # Non-app single-word "open ..." that isn't resolvable stays unknown.
        self.expected({
            "open the door": "unknown",
            "open the window": "unknown",
        })

    def test_open_url(self):
        self.expected({
            "open url https://example.com": "open_url",
        })

    def test_powershell(self):
        self.expected({
            "launch powershell": "powershell",
        })

    def test_create_folder(self):
        self.expected({
            "create folder C:/temp/newdir": "create_folder",
        })

    def test_screenshot(self):
        self.expected({
            "take a screenshot": "screenshot",
            "capture the screen": "screenshot",
        })

    def test_media_control(self):
        self.expected({
            "turn volume up": "media_control",
            "mute everything": "media_control",
            "set volume to 50": "media_control",
            "play the track": "media_control",
            "skip to next": "media_control",
        })
        # "media" alone must not hijack other commands like planning/scheduling.
        self.expected({
            "make a media content plan": "planning",
            "set a media reminder": "schedule",
        })

    def test_schedule(self):
        self.expected({
            "remind me to call bob": "schedule",
            "set a routine": "schedule",
            "add a todo": "schedule",
            "remind me to drink water at 2pm": "schedule",
        })

    def test_ui_action(self):
        self.expected({
            "click the login button": "ui_action",
            "press enter": "ui_action",
            "type hello": "ui_action",
            "scroll down": "ui_action",
            "open notepad": "open_app",  # must not collide
            "create file test.txt": "create_file",
        })

    def test_complex_reasoning(self):
        self.expected({
            "analyze this spreadsheet": "complex_reasoning",
            "convert the csv to excel": "complex_reasoning",
        })

    def test_planning(self):
        self.expected({
            "plan my day": "planning",
            "automate this workflow": "planning",
        })

    def test_unknown(self):
        self.expected({
            "hello there": "unknown",
            "what time is it": "unknown",
        })

    def test_open_file_not_misread_as_app(self):
        # "open" + a known file extension must never fall through to app
        self.expected({
            "open chrome.pdf": "open_file",
            "open paint.docx": "open_file",
        })


if __name__ == "__main__":
    unittest.main()
