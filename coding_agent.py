# agent.py
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()          # reads OPENAI_API_KEY in THIS process only
MODEL = "gpt-4.1"
WORKSPACE = Path("workspace").resolve()
WORKSPACE.mkdir(exist_ok=True)

ALLOWED_PACKAGES = {"fastapi", "httpx", "pytest", "uvicorn", "pydantic", "sqlalchemy"}


# ---------- Safe environment for child processes ----------
def build_safe_env() -> dict:
    """Only pass what a child process truly needs. No API keys, no tokens."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(WORKSPACE),              # keeps ~ pointing at the workspace
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "LANG": "C.UTF-8",
    }
    # Windows needs these to run Python at all
    for key in ("SYSTEMROOT", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env

SAFE_ENV = build_safe_env()


# ---------- Safe subprocess runner ----------
def run_safe(cmd: list[str], timeout: int = 30) -> str:
    """Run a fixed argument list (shell=False) with a clean env and hard timeout."""
    popen_kwargs = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True   # own process group

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=WORKSPACE,
            env=SAFE_ENV,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            **popen_kwargs,
        )
    except FileNotFoundError as e:
        return f"ERROR: could not start process: {e}"

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # kill the child AND anything it spawned
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.communicate()
        return f"Timed out after {timeout}s and was killed. Don't start long-running servers."

    out = f"exit_code={proc.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
    return out[:4000]


# ---------- File tools ----------
def safe_path(rel: str) -> Path:
    p = (WORKSPACE / rel).resolve()
    if not p.is_relative_to(WORKSPACE):
        raise ValueError("Path escapes workspace")
    return p

def write_file(path: str, content: str) -> str:
    p = safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {path} ({len(content)} chars)"

def read_file(path: str) -> str:
    return safe_path(path).read_text(encoding="utf-8")[:8000]

def list_files() -> str:
    files = sorted(
        str(f.relative_to(WORKSPACE))
        for f in WORKSPACE.rglob("*")
        if f.is_file() and "__pycache__" not in f.parts
    )
    return "\n".join(files) or "(empty)"

def edit_file(path: str, old_text: str, new_text: str) -> str:
    p = safe_path(path)
    if not p.exists():
        return f"ERROR: {path} does not exist. Use write_file to create it."
    content = p.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count == 0:
        return "ERROR: old_text not found. Use read_file and copy the text exactly."
    if count > 1:
        return f"ERROR: old_text appears {count} times. Include more surrounding lines."
    p.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    return f"Edited {path}"


# ---------- Execution tools (fixed commands, no shell) ----------
def run_tests() -> str:
    return run_safe([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])

def run_python_file(path: str) -> str:
    p = safe_path(path)
    if p.suffix != ".py":
        return "ERROR: only .py files can be run."
    if not p.exists():
        return f"ERROR: {path} does not exist."
    return run_safe([sys.executable, str(p)])

def install_package(name: str) -> str:
    if name not in ALLOWED_PACKAGES:
        return f"ERROR: '{name}' not allowed. Allowed: {sorted(ALLOWED_PACKAGES)}"
    return run_safe([sys.executable, "-m", "pip", "install", name], timeout=120)


TOOL_FUNCS = {
    "write_file": write_file,
    "read_file": read_file,
    "list_files": list_files,
    "edit_file": edit_file,
    "run_tests": run_tests,
    "run_python_file": run_python_file,
    "install_package": install_package,
}


# ---------- Tool schemas ----------
def tool(name: str, description: str, props: dict | None = None, required: list | None = None):
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": props or {},
            "required": required or [],
        },
    }}

TOOLS = [
    tool("write_file", "Create or overwrite a file in the workspace.",
         {"path": {"type": "string", "description": "Relative path, e.g. routes/items.py"},
          "content": {"type": "string"}}, ["path", "content"]),
    tool("read_file", "Read a file from the workspace.",
         {"path": {"type": "string"}}, ["path"]),
    tool("list_files", "List all files in the workspace."),
    tool("edit_file", "Replace one exact, unique snippet in an existing file. Prefer this over write_file for small fixes.",
         {"path": {"type": "string"},
          "old_text": {"type": "string", "description": "Exact text to find (must appear exactly once)"},
          "new_text": {"type": "string"}}, ["path", "old_text", "new_text"]),
    tool("run_tests", "Run pytest in the workspace and return the output."),
    tool("run_python_file", "Run a single .py file in the workspace and return its output.",
         {"path": {"type": "string"}}, ["path"]),
    tool("install_package", f"pip install one allowed package. Allowed: {sorted(ALLOWED_PACKAGES)}",
         {"name": {"type": "string"}}, ["name"]),
]

SYSTEM_PROMPT = """You are a coding agent working inside a sandboxed workspace folder.
Rules:
- Plan briefly, then build step by step, writing ONE file at a time with write_file.
- Never just print code in chat; always save it using write_file.
- For small fixes use edit_file instead of rewriting the whole file.
- After writing code, run it with run_python_file or run_tests and fix any errors.
- Install missing packages with install_package (only allowed ones).
- For web APIs, do NOT start a server. Write pytest tests using FastAPI's TestClient.
- Stop only when the code runs correctly, then give a short summary.
"""


# ---------- Agent loop ----------
def run_agent(user_prompt: str, max_steps: int = 25):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    for step in range(max_steps):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS,
        )
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            print("\n✅ DONE:\n", msg.content)
            return

        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
                print(f"🔧 [{step}] {name}({', '.join(args)})")
                if name not in TOOL_FUNCS:
                    result = f"ERROR: unknown tool '{name}'"
                else:
                    result = TOOL_FUNCS[name](**args)
            except Exception as e:
                result = f"ERROR: {type(e).__name__}: {e}"

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": str(result),
            })

    print("⚠️ Reached max steps")


if __name__ == "__main__":
    run_agent(input("What should I build? > "))