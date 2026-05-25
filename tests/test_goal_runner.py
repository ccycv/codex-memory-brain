import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PLUGIN_ROOT / "opencode-goal" / "goal-runner.py"


def load_runner_module():
    spec = importlib.util.spec_from_file_location("goal_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runner = load_runner_module()


class GoalRunnerTests(unittest.TestCase):
    def test_parse_jsonl_extracts_session_tools_text_and_errors(self):
        output = "\n".join(
            [
                json.dumps({"sessionID": "ses_test", "type": "tool_use", "part": {"tool": "goal_update"}}),
                json.dumps({"type": "text", "part": {"text": "goal updated"}}),
                json.dumps({"type": "step_error", "message": "failed"}),
                "not-json",
            ]
        )
        parsed = runner.parse_jsonl(output)

        self.assertEqual(parsed["session_id"], "ses_test")
        self.assertEqual(parsed["tools"], ["goal_update"])
        self.assertEqual(parsed["texts"], ["goal updated"])
        self.assertEqual(len(parsed["errors"]), 2)
        self.assertEqual(parsed["events"], 3)

    def test_mark_goal_pauses_and_appends_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            goal_home = Path(tmp)
            session_id = "ses_test"
            runner.write_goal(
                goal_home,
                session_id,
                {
                    "objective": "Ship feature",
                    "status": "active",
                    "progress": "",
                    "history": [{"event": "set"}],
                },
            )

            updated = runner.mark_goal(goal_home, session_id, "paused", "safety limit reached")

            self.assertEqual(updated["status"], "paused")
            self.assertEqual(updated["progress"], "safety limit reached")
            self.assertEqual(updated["history"][-1]["event"], "runner_paused")
            saved = runner.read_goal(goal_home, session_id)
            self.assertEqual(saved["status"], "paused")

    def test_paused_is_terminal_status(self):
        self.assertIn("paused", runner.TERMINAL_STATUSES)


if __name__ == "__main__":
    unittest.main()
