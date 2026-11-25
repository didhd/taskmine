"""taskmine's default Strands Agents coding agent, for Harbor's `strands` agent.

    harbor run -a strands -m amazon-bedrock/<model> --ak factory_path=<this file> --ak factory=taskmine_strands:create_agent

Harbor's Strands runner builds the model from `-m` and a sandbox for the task's working directory,
then calls `create_agent(sandbox, model=...)`. Harbor ships no default factory, so this one gives the
agent what the other coding agents have: a shell and a file editor in the repository (the runner's
own sandbox tools), and a short system prompt. It adds no tools, examples or hints of its own.
"""

from strands import Agent

SYSTEM_PROMPT = (
    "You are a coding agent working in a software repository. Use the shell and the file editor to read the "
    "code, make the change the task asks for, and run the tests that cover it. Work until the change is done, "
    "then stop and say what you changed."
)


def create_agent(sandbox, model=None, **kwargs):
    return Agent(model=model, tools=sandbox.get_tools(), system_prompt=SYSTEM_PROMPT, callback_handler=None, **kwargs)
