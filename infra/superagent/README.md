# DeepSeek Super-agent for RAJ development

The [DeepSeek Super-agent prototype](https://github.com/deepseek-ai/dsh-superagent-prototype) works on **this repository only**. It answers issues and comments from allowed people with pull requests, and it fixes red CI on `main`. It is a development tool. It never touches the finance service, client connectors or client data.

## Separation from production

| | Super-agent (development) | RAJ finance service (production) |
|---|---|---|
| Machine / OS user | Dedicated dev user (e.g. `raj-dev`) | `raj` service user on the firm server |
| GitHub identity | Dedicated agent account, write access to this repo only | none |
| Credentials | `gh-agent` wrapper only | server PKI, SSH tunnel accounts, `DEEPSEEK_API_KEY` |
| Data | `fixtures/`, `tests/` (synthetic) | client books via connectors |
| Merges | none: `vrajkher` reviews and merges every PR | signed releases only |

The worker preset runs with full access as its OS user, so the dedicated OS user is the real isolation. Never run it as the `raj` service user, and never on a client PC.

## Setup (once, about 30 minutes)

1. **Accounts (you).**
   1. Create a GitHub user such as `raj-dev-agent` and give it **write** access to `vrajkher/Supererpagent` only.
   2. On the dev machine, as the dev user, run:
      ```sh
      mkdir -p ~/.local/bin
      printf '#!/bin/sh\nGH_CONFIG_DIR="$HOME/.config/gh-agent" exec gh "$@"\n' > ~/.local/bin/gh-agent
      chmod +x ~/.local/bin/gh-agent
      gh-agent auth login           # sign in as the agent account
      gh-agent api user --jq .login # must print the agent login
      ```
2. **Install.** You need Node 22.19+ or 24+, pnpm, git 2.32+, tmux, Python 3.11+ and gh 2.48+.
   ```sh
   npm install -g @deepseek-ai/dsh@0.2.1-rc.3
   git clone https://github.com/deepseek-ai/dsh-superagent-prototype ~/src/dsh-superagent && cd ~/src/dsh-superagent
   pnpm install && pnpm build
   dsh superagent --from-default-profile web --dump-config > /dev/null   # new installs only
   dsh plugin --profile superagent add "link:$PWD"
   git clone https://github.com/vrajkher/Supererpagent ~/src/Supererpagent   # the clone tickets check out from
   ```
3. **Model.** Give the `superagent` profile a DeepSeek provider. Use a separate API key from the one the finance service uses, so development spend and access stay apart.
4. **Profile.** Copy `profile.cordis.patch.yml` to `~/.dsh/profiles/superagent/cordis.patch.yml` and set `stateDir`.
5. **Start.**
   ```sh
   cd ~/src/dsh-superagent
   scripts/dsh-superagent start
   scripts/dsh-superagent status
   ```
6. **Settings.**
   1. In `settings/*.json`, set the `gh-agent` path, the work directory and the clone path (`~/src/Supererpagent`, absolute).
   2. Run `infra/superagent/apply-settings.sh`.
   3. Paste `prompts/delivery.md` and `prompts/scope.md` into **Settings → Prompts** as the Delivery requirements and Scope team sections.
7. **Hourly check.** Create the hourly check from the Super-agent docs (`docs/006-deployment.md`, "Hourly check") in your normal DSH profile.

The policy starts in `observe` accept mode with one rule (fix red CI on `main`), and `vrajkher` is the only allowed requester. Widen it only after the agent's PRs have been consistently right.

## Use

On an issue or a PR in this repository, comment `@<agent login> <request>`. For example:

> `@raj-dev-agent add a test that a reconciliation with a duplicate statement row never drafts a voucher for the duplicate`

A Ticket appears in the panel (`http://127.0.0.1:3082`, **Superagent** in the sidebar) within about a minute. The agent works in its own worktree and replies on the issue with a draft PR.

Every ticket's worker reads the repository `AGENTS.md`, which holds the coding rules and the finance safety rules.
