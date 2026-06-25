from distr.core.agent.services.llm.base_service import BaseLLMService


def test_normalize_tool_kwargs_raises_on_missing_required():
    class DummyTool:
        name = "some_other_tool"

        def _run(self, input_path: str, another_field: str):
            return "ok"

    tool = DummyTool()
    try:
        BaseLLMService._normalize_tool_kwargs(tool, {})
    except TypeError as exc:
        assert "some_other_tool._run() missing required argument(s): input_path, another_field" in str(exc)
    else:
        raise AssertionError("Expected TypeError for missing required arguments")


def test_normalize_tool_kwargs_tolerates_convert_document_missing_required():
    class DummyTool:
        name = "convert_document"

        def _run(self, input_path: str, output_format: str, output_path: str = "x"):
            return "ok"

    tool = DummyTool()
    args = BaseLLMService._normalize_tool_kwargs(tool, {})
    assert args["input_path"] is None
    assert args["output_format"] is None


def test_normalize_tool_kwargs_keeps_known_arguments():
    class DummyTool:
        name = "convert_document"

        def _run(self, input_path: str, output_format: str, output_path: str = "x", **kwargs):
            return "ok"

    tool = DummyTool()
    args = BaseLLMService._normalize_tool_kwargs(tool, {"input_path": "a.md", "output_format": "pdf", "extra": "y"})
    assert args["input_path"] == "a.md"
    assert args["output_format"] == "pdf"
    assert args["extra"] == "y"
