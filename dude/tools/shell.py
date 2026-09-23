"""Shell & code execution tools (always permission-gated)."""
import subprocess

from .registry import ToolResult, register_tool


@register_tool("run_command", "Run a shell command and return its output",
               permission="ask")
def run_command(command: str, timeout: int = 30) -> ToolResult:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        return ToolResult(
            result.returncode == 0,
            output.strip() or f"Command exited with code {result.returncode}",
            data={"code": result.returncode},
        )
    except subprocess.TimeoutExpired:
        return ToolResult(False, "Command timed out.")
    except Exception as exc:
        return ToolResult(False, f"Command failed: {exc}")


@register_tool("run_python", "Run a Python snippet and return its output",
               permission="ask")
def run_python(code: str, timeout: int = 30) -> ToolResult:
    try:
        result = subprocess.run(
            ["python", "-c", code], capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        return ToolResult(
            result.returncode == 0,
            output.strip() or f"Python exited with code {result.returncode}",
            data={"code": result.returncode},
        )
    except subprocess.TimeoutExpired:
        return ToolResult(False, "Python execution timed out.")
    except Exception as exc:
        return ToolResult(False, f"Python execution failed: {exc}")

