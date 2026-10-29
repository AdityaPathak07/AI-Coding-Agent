# Code Agent

A minimal AI coding agent built on the OpenAI API. Give it a prompt, and it
writes files, runs them, reads the errors, and fixes its own code until it works.

## How it works

The agent uses **tool calling** in a loop:

```
Prompt → LLM → requests a tool (write_file, run_tests, ...)
         ↑                          ↓
         └──── result/error ←── your code executes it
```

The model is the brain and `agent.py` is the hands. The loop ends when the
model replies without requesting more tools.

## Features

- Creates multi-file projects (e.g. FastAPI apps with several routes)
- Iterates: runs code and tests, reads failures, edits files, re-runs
- Targeted edits with `edit_file` instead of rewriting whole files
- Path sandboxing: file tools cannot escape the `workspace/` folder
- No `shell=True`: only fixed, allowlisted commands
- Child processes run with a stripped environment (API key not exposed)
- Timeouts kill the entire process group

## Tools available to the model

| Tool | Purpose |
|---|---|
| `write_file` | Create or overwrite a file |
| `read_file` | Read a file |
| `list_files` | Show the project tree |
| `edit_file` | Exact find-and-replace in a file |
| `run_tests` | Run `pytest` |
| `run_python_file` | Run a single `.py` file |
| `install_package` | `pip install` from an allowlist |

## Quick start

```bash
git clone https://github.com/<your-username>/AI-Coding-Agent.git
cd code-agent

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # then add your OpenAI API key
python agent.py
```

## Example prompts

```
Write Python code to print primes up to 100 in primes.py and run it
```

```
Build a FastAPI app with 5 routes (CRUD for items + health check), split into
main.py, routes/items.py and models.py. Write pytest tests using TestClient
and make sure they pass.
```

Generated files appear in the `workspace/` folder.

## Configuration

| Setting | Location | Default |
|---|---|---|
| Model | `MODEL` in `agent.py` | `gpt-4.1` |
| Max steps | `run_agent(max_steps=...)` | `25` |
| Command timeout | `run_safe(timeout=...)` | `30s` |
| Allowed packages | `ALLOWED_PACKAGES` | fastapi, httpx, pytest, ... |

## Security

This project executes AI-generated code. Please read before using it.

**What it protects against**
- Path traversal in file tools (`safe_path`)
- Shell injection (`shell=False`, no free-form commands)
- Leaking `OPENAI_API_KEY` to generated code (clean child environment)
- Hung or runaway processes (timeouts and process-group kill)

**What it does NOT protect against**
- Generated code runs as **your OS user**. It can read files your account can
  read (by absolute path), use the network, and delete files outside `workspace/`.
- Prompt injection if you feed the agent untrusted content.

**Recommendations**
- Use a virtual environment.
- Don't run it on a machine with sensitive files unless it's in a container or VM.
- Don't expose it to prompts from untrusted users.
- Set a spending limit on your OpenAI account.

## Limitations

- Cannot run long-lived servers (use FastAPI's `TestClient` instead)
- Output is truncated to 4000 characters per command
- Quality depends on the model and the prompt

## Roadmap

- [ ] Docker-based sandbox for code execution
- [ ] Plan-first step (file list before building)
- [ ] Confirmation prompt before execution
- [ ] OpenAI Agents SDK version

## Contributing

Issues and pull requests are welcome.
