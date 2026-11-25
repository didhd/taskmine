# Security policy

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**
(https://github.com/didhd/taskmine/security/advisories/new). Do not open a public issue or pull
request for it. Include the taskmine version (`taskmine --version`), what an attacker controls,
what they gain, and the steps to reproduce. The maintainer replies in the advisory and credits
you there unless you ask otherwise.

## Supported versions

Fixes go into the latest release only. While taskmine is 0.x, upgrade to the newest version to
get them.

## What counts

taskmine runs untrusted code: the agents under test, and the code and tests of the repositories it
mines. These are in scope:

- a cloud credential reaching a task or agent container, from `taskmine gateway`, `bench`,
  `verify` or a task image;
- the gateway serving a request from outside loopback and private networks, or forwarding the
  caller's own credentials;
- a task snapshot holding repository history or any file content from the change it asks for;
- an instruction that contains the solution despite the leak check;
- an agent passing a task by editing the hidden tests or the test side of the repository, which
  the grader restores before it runs;
- a path in a repository or an archive that makes taskmine write outside its working directory.

Out of scope: what a recipe you wrote runs (its install and test commands run as you wrote them),
and an agent that tampers with the runtime inside its own container (node_modules, the
interpreter). The grader shares that container and cannot rule it out; `grader.py` says so.
