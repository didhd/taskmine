"""taskmine's default Strands Agents coding agent, for Harbor's `strands` agent.

    harbor run -a strands -m amazon-bedrock/<model> --ak factory_path=<this file> \
        --ak factory=taskmine_strands:create_agent [--ak 'factory_kwargs={"model": "<bedrock model id>"}']

Harbor's Strands runner builds a sandbox for the task's working directory, then calls
`create_agent(sandbox, model=...)`. Harbor ships no default factory, so this one gives the agent
what the other coding agents have: a shell and a file editor in the repository (the runner's own
sandbox tools), and a short system prompt. It adds no tools, examples or hints of its own.

`model` is either the model the runner built from `-m`, or, when taskmine passes it in
factory_kwargs, a Bedrock model ID. The second form is for sessions where the AWS credentials
exist only inside the sandbox (AgentCore): Harbor cannot see them, so it would not pick the
Bedrock provider. A Bedrock model built here uses the session's own credentials and AWS_REGION.
"""

import os

from strands import Agent

SYSTEM_PROMPT = (
    "You are a coding agent working in a software repository. Use the shell and the file editor to read the "
    "code, make the change the task asks for, and run the tests that cover it. Work until the change is done, "
    "then stop and say what you changed."
)


# Output tokens per model call. Strands' Bedrock default stopped 4 of 8 trials with
# MaxTokensReachedException while the agent wrote a file; 32,000 is Claude Code's default.
MAX_TOKENS = int(os.environ.get("TASKMINE_STRANDS_MAX_TOKENS", "32000"))


def _heartbeat(every: float = 60.0) -> None:
    """One line on stdout a minute. The runner writes its results to files and prints nothing, and
    AgentCore ends a command that sends no byte for 15 minutes (measured: 1 of 8 trials)."""
    import threading
    import time

    def beat():
        while True:
            time.sleep(every)
            print("taskmine-strands: running", flush=True)
    threading.Thread(target=beat, daemon=True).start()


def create_agent(sandbox, model=None, **kwargs):
    _heartbeat()
    if isinstance(model, str):
        from strands.models.bedrock import BedrockModel
        model = BedrockModel(model_id=model, region_name=os.environ.get("AWS_REGION") or None, max_tokens=MAX_TOKENS)
    return Agent(model=model, tools=sandbox.get_tools(), system_prompt=SYSTEM_PROMPT, callback_handler=None, **kwargs)
