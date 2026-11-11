import importlib.util
import inspect
import pathlib
import unittest


def _dude_entrypoint():
    path = pathlib.Path(__file__).resolve().parents[3] / "dude.py"
    spec = importlib.util.spec_from_file_location("dude_entrypoint_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestVoiceRouting(unittest.TestCase):
    def test_wake_name_is_required_for_voice_turns(self):
        dude = _dude_entrypoint()

        self.assertTrue(dude._has_wake_name("Dude"))
        self.assertTrue(dude._has_wake_name("hey dude check this"))
        self.assertFalse(dude._has_wake_name("check this employee portal"))
        self.assertFalse(dude._has_wake_name("nude check this"))
        self.assertFalse(dude._has_wake_name("dud check this"))

    def test_bare_dude_is_a_valid_voice_turn(self):
        dude = _dude_entrypoint()

        self.assertTrue(dude._has_wake_name("dude"))
        self.assertTrue(dude._has_wake_name("Dude."))
        self.assertTrue(dude._has_wake_name("the word dude"))

    def test_realtime_addressing_is_strict_for_microphone_input(self):
        from core.ambient import has_wake_word

        accepted = (
            "Dude",
            "dude",
            "Dude.",
            "Dude, check my employee portal",
        )
        rejected = (
            "check my employee portal",
            "dudes, check my employee portal",
            "dudette, check my employee portal",
            "theDude, check my employee portal",
            "nude check my employee portal",
        )
        for transcript in accepted:
            with self.subTest(transcript=transcript):
                self.assertTrue(has_wake_word(transcript))
        for transcript in rejected:
            with self.subTest(transcript=transcript):
                self.assertFalse(has_wake_word(transcript))

    def test_screen_lookup_question_is_computer_work(self):
        dude = _dude_entrypoint()

        self.assertTrue(
            dude._is_action_task(
                "Dude, check whether my boss gave me any tasks in my employee portal"
            )
        )
        self.assertFalse(dude._is_action_task("Dude, why is the sky blue?"))

        from core.orchestrator.goal_executor import CognitiveFrontDoor
        action, informational = CognitiveFrontDoor._request_shape(
            "Dude, check whether my boss gave me any tasks in my employee portal"
        )
        self.assertTrue(action)
        self.assertFalse(informational)

        # Exercise the front-door translation seam as well as the lower-level
        # shape predicate: a yes/no portal lookup must still be computer work.
        front_door = CognitiveFrontDoor.__new__(CognitiveFrontDoor)
        interpretation = front_door._translate_routing_result(
            None, {"transcript":
                   "Dude, check whether my boss gave me any tasks in my employee portal"}
        )
        self.assertEqual(interpretation.intent_kind.value, "computer_work")
        self.assertTrue(interpretation.requires_computer_action)

    def test_promise_classifier_distinguishes_stalling_from_completion(self):
        from core.brain import _is_promise

        for reply in (
            "I'm on it.",
            "Let me check the portal now.",
            "I will open the browser and look.",
        ):
            with self.subTest(reply=reply):
                self.assertTrue(_is_promise(reply))
        for reply in (
            "I checked the portal and found no new tasks.",
            "Done.",
            "I cannot do this autonomously.",
        ):
            with self.subTest(reply=reply):
                self.assertFalse(_is_promise(reply))

    def test_live_boundary_routes_screen_work_without_model_classification(self):
        from core.orchestrator.realtime_voice import _request_requires_computer_work

        self.assertTrue(_request_requires_computer_work(
            "Dude, check whether my boss gave me any tasks in my employee portal"))
        self.assertTrue(_request_requires_computer_work(
            "Dude, read what is currently on my screen"))
        self.assertFalse(_request_requires_computer_work(
            "Dude, how are you today?"))

    def test_greeting_with_trailing_wake_word_stays_local(self):
        from core.orchestrator.realtime_voice import (
            _chat_result_text, _local_casual_reply)
        from core.reflex import route

        expected = "Good morning, sir. I'm here and ready."
        self.assertEqual(_local_casual_reply("good morning dude"), expected)
        self.assertEqual(_local_casual_reply("Good morning, dude."), expected)
        self.assertEqual(route("good morning dude"),
                         {"kind": "reply", "reply": expected})
        self.assertEqual(_chat_result_text(
            {"role": "assistant", "content": "fallback reply"}),
                         "fallback reply")
        self.assertEqual(_chat_result_text((
            {"role": "assistant", "content": "tuple reply"}, False)),
                         "tuple reply")

    def test_normal_startup_does_not_speak_boot_greeting(self):
        dude = _dude_entrypoint()

        # Startup is intentionally silent. Greetings remain available only
        # after a user-addressed turn through the local social router.
        run_agent_source = inspect.getsource(dude.run_agent)
        self.assertNotIn("boot_greeting(", run_agent_source)

    def test_duplicate_app_guards_classify_open_and_close_reopen_loops(self):
        from core.tools import (
            _note_app_closed,
            _note_app_opened,
            _reset_app_turn,
            app_close_then_reopen_block,
            app_open_block,
        )

        _reset_app_turn()
        try:
            self.assertIsNone(app_open_block("Comet"))
            _note_app_opened("Comet")
            self.assertIn("already opened", app_open_block("Comet"))

            _note_app_closed("Comet")
            self.assertIn("just CLOSED", app_close_then_reopen_block("Comet"))
        finally:
            _reset_app_turn()

    def test_sir_suffix_is_added_once(self):
        from core.voice import _ensure_sir

        self.assertEqual(_ensure_sir("Done."), "Done, sir.")
        self.assertEqual(_ensure_sir("Done, sir."), "Done, sir.")


if __name__ == "__main__":
    unittest.main()
