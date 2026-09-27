"""Run after configuring FLORA_MODEL / FLORA_BASE_URL / OPENAI_API_KEY."""

from pathlib import Path

from flora.general import GeneralAgent

workspace = Path("workspace")
workspace.mkdir(exist_ok=True)
with GeneralAgent(workspace=workspace, session_dir="sessions/research") as agent:
    result = agent.run(
        "List the workspace contents and explain what tasks you can perform with them."
    )
    print(result["status"])
    print(result.get("value"))
