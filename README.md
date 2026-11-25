# taskmine

**Benchmark coding agents on your own repo.**

Taskmine turns your team's merged pull requests into [Harbor](https://github.com/laude-institute/harbor) tasks. Keep the tasks and tests fixed, then swap the model or coding agent to see what works on your code.

https://github.com/user-attachments/assets/ee577b45-6332-4d3c-a2bf-7e55314ccbfc

**2:16 demo.** The full run took 5:04 on AgentCore V1. The video skips a wait during verification. [Full recording](https://github.com/didhd/taskmine/issues/1#issuecomment-7152029102) · [Terminal cast](docs/demo.cast).

## Try it

From this checkout, run `uv tool install .`. Then start in an empty directory with GitHub CLI and AWS access configured. Replace `your-org/your-repo` with your repo.

```sh
# Sign in, clone your repo, and create taskmine.toml.
taskmine login
taskmine init your-org/your-repo

# Pick the agents you want to compare.
taskmine agents add claude-code --provider bedrock --model us.anthropic.claude-sonnet-5-5
taskmine agents add codex --provider bedrock --model us.openai.gpt-6.1-terra
taskmine agents add opencode --provider bedrock --model us.anthropic.claude-sonnet-5-5
taskmine agents add strands --provider bedrock --model us.anthropic.claude-sonnet-5-5

# Review taskmine.toml, then draft tasks from recent changes.
taskmine mine --limit 20

# Set up AWS resources and keep only tasks that pass validation.
taskmine agentcore setup
taskmine verify

# Run the same sample of up to 20 tasks with every agent.
taskmine bench --tasks 20
taskmine results

# After reviewing the AWS resource list, uncomment teardown to clean up.
taskmine agentcore resources
# taskmine agentcore teardown
```

[Cleanup](docs/guide.md#agentcore-runtime) applies to the AWS prefix, not just this project.

AgentCore V1 is the default, so you don't need local Docker. The [usage guide](docs/guide.md) covers AWS setup, other agents, custom test runners, V2, and Docker.

## What makes a task count?

Taskmine starts at the commit before the fix. It only keeps tasks where the tests fail without the fix and pass with it. Harbor's `nop` must score 0 and `oracle` must score 1.

On a TypeScript monorepo, **552 candidate PRs yielded 173 verified tasks**. All 173 passed both Harbor checks. [See the run and what we learned](docs/DOGFOOD.md).

[Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [MIT-0](LICENSE)
