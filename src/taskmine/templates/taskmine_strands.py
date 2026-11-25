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


def create_agent(sandbox, model=None, **kwargs):
    if isinstance(model, str):
        from strands.models.bedrock import BedrockModel
        model = BedrockModel(model_id=model, region_name=os.environ.get("AWS_REGION") or None)
    return Agent(model=model, tools=sandbox.get_tools(), system_prompt=SYSTEM_PROMPT, callback_handler=None, **kwargs)
