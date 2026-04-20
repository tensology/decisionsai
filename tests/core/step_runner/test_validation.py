"""Unit tests for StepValidator."""

import unittest

from distr.core.workflow_engine.validation import StepValidator, ValidationError


class TestStepValidator(unittest.TestCase):

    def setUp(self):
        self.validator = StepValidator()

    # --- Unknown step type ---

    def test_unknown_step_type_returns_error(self):
        errors = self.validator.validate("nonexistent_type", {})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "step_type")
        self.assertIn("Unknown step type", errors[0].message)

    # --- Run Command ---

    def test_run_command_valid(self):
        errors = self.validator.validate("run_command", {"command": "echo hello"})
        self.assertEqual(errors, [])

    def test_run_command_empty_command(self):
        errors = self.validator.validate("run_command", {"command": ""})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "command")

    def test_run_command_missing_command(self):
        errors = self.validator.validate("run_command", {})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "command")

    def test_run_command_whitespace_only(self):
        errors = self.validator.validate("run_command", {"command": "   "})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "command")

    # --- Play Recording ---

    def test_play_recording_with_id(self):
        errors = self.validator.validate("play_recording", {"recording_id": 1})
        self.assertEqual(errors, [])

    def test_play_recording_with_name(self):
        errors = self.validator.validate("play_recording", {"recording_name": "my rec"})
        self.assertEqual(errors, [])

    def test_play_recording_with_both(self):
        errors = self.validator.validate("play_recording", {"recording_id": 1, "recording_name": "rec"})
        self.assertEqual(errors, [])

    def test_play_recording_missing_both(self):
        errors = self.validator.validate("play_recording", {})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "recording")

    def test_play_recording_empty_name(self):
        errors = self.validator.validate("play_recording", {"recording_name": "  "})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "recording")

    # --- HTTP Request ---

    def test_http_request_valid(self):
        errors = self.validator.validate("http_request", {"url": "https://example.com"})
        self.assertEqual(errors, [])

    def test_http_request_http_url(self):
        errors = self.validator.validate("http_request", {"url": "http://example.com"})
        self.assertEqual(errors, [])

    def test_http_request_empty_url(self):
        errors = self.validator.validate("http_request", {"url": ""})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "url")

    def test_http_request_missing_url(self):
        errors = self.validator.validate("http_request", {})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "url")

    def test_http_request_bad_url_scheme(self):
        errors = self.validator.validate("http_request", {"url": "ftp://example.com"})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "url")
        self.assertIn("http://", errors[0].message)

    def test_http_request_valid_method(self):
        errors = self.validator.validate("http_request", {"url": "https://x.com", "method": "POST"})
        self.assertEqual(errors, [])

    def test_http_request_invalid_method(self):
        errors = self.validator.validate("http_request", {"url": "https://x.com", "method": "INVALID"})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "method")

    def test_http_request_no_method_is_ok(self):
        errors = self.validator.validate("http_request", {"url": "https://x.com"})
        self.assertEqual(errors, [])

    # --- Execute Code ---

    def test_execute_code_with_instruction(self):
        errors = self.validator.validate("execute_code", {"instruction": "print hello"})
        self.assertEqual(errors, [])

    def test_execute_code_with_code(self):
        errors = self.validator.validate("execute_code", {"code": "print('hello')"})
        self.assertEqual(errors, [])

    def test_execute_code_with_both(self):
        errors = self.validator.validate("execute_code", {"instruction": "do it", "code": "x=1"})
        self.assertEqual(errors, [])

    def test_execute_code_missing_both(self):
        errors = self.validator.validate("execute_code", {})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "instruction")

    def test_execute_code_empty_both(self):
        errors = self.validator.validate("execute_code", {"instruction": "", "code": ""})
        self.assertEqual(len(errors), 1)

    # --- Playwright ---

    def test_playwright_with_instruction(self):
        errors = self.validator.validate("playwright", {"instruction": "click button"})
        self.assertEqual(errors, [])

    def test_playwright_with_code(self):
        errors = self.validator.validate("playwright", {"code": "page.click('btn')"})
        self.assertEqual(errors, [])

    def test_playwright_missing_both(self):
        errors = self.validator.validate("playwright", {})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "instruction")
        self.assertIn("Playwright", errors[0].message)

    # --- ValidationError structure ---

    def test_validation_error_has_field_and_message(self):
        errors = self.validator.validate("run_command", {})
        self.assertIsInstance(errors[0], ValidationError)
        self.assertIsInstance(errors[0].field, str)
        self.assertIsInstance(errors[0].message, str)
        self.assertTrue(len(errors[0].field) > 0)
        self.assertTrue(len(errors[0].message) > 0)


if __name__ == "__main__":
    unittest.main()
