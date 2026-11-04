"""Example custom tool. Copy it to ~/.nar/tools/ or to .nar/tools/ in a project."""

from nar.tools import Tool, ToolContext, ToolResult


class WordCountTool(Tool):
    name = "word_count"
    description = "Count the lines and words of a text file."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File to count."}},
        "required": ["path"],
        "additionalProperties": False,
    }
    # This tool only reads. Set this to True in a tool that changes anything.
    requires_approval = False

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        path = context.cwd / arguments["path"]
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResult.error(f"cannot read {arguments['path']}: {exc}")
        lines, words = len(text.splitlines()), len(text.split())
        return ToolResult.ok(f"{lines} lines, {words} words", summary=f"{words} words")
