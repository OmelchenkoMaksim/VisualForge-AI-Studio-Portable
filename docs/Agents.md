# Agent mode (Text tab)

The Text tab has a «Чат | Агент» switch. In agent mode a local coding agent works inside one project folder: it reads and edits
files, runs commands and uses git, asking the user before actions the chosen permission level does not allow. Everything runs
offline on the private Text model.

## Agents

| Agent | State | Installed as |
|---|---|---|
| OpenCode 1.19.4 (MIT) | connected | `Runtime/Agents/opencode/opencode.exe` (official GitHub release `opencode-windows-x64.zip`, SHA-256 `93b89927…fdf62e5`, Authenticode signed by Anomaly Innovations) |
| Aider 0.86.2 (Apache-2.0) | connected | `Runtime/Agents/aider`: embeddable Python 3.12.10 + `aider-chat` and 108 pinned wheels (about 624 MB on disk) |
| DeepSeek Harness 0.2.1-rc.2 (MIT, developer preview) | connected | `Runtime/Agents/dsh`: portable Node.js 24.21.0 + 609 pinned npm packages (about 568 MB on disk) |

Setup installs both as software components of the Text module (`App/config/components.json`, catalog `2026.10.27`):

- `text.agent.opencode`: the pinned archive (81,744,896 bytes, the SHA-256 above, mirrored by the GitHub asset API URL) is
  unpacked to `Runtime/Agents/opencode`.
- `text.agent.aider`: python.org embeddable zip, pip, then `pip install --no-index --no-deps` of the wheels listed with size and
  SHA-256 in `App/config/aider-assets.json` (130.8 MB download). The wheel set was resolved once with
  `pip install --dry-run --report --only-binary=:all: --python-version 3.12 --platform win_amd64 aider-chat==0.86.2`;
  `tools/build_catalog.py` (`build_aider`) turns the manifest into the catalog entry.

- `text.agent.dsh`: Node.js zip from nodejs.org (only `node.exe` and its licence are kept), then every npm tarball of the
  resolved win32-x64 tree unpacked to its own `node_modules` path (`App/config/dsh-assets.json`: path, version, size,
  SHA-256; 133.5 MB download; alternatives `nodejs.org/download/release` and `registry.yarnpkg.com`). The tree was resolved with
  `npm install --package-lock-only` + `npm ci --ignore-scripts`; the installed tree matched it file for file. Install scripts are
  not run: on Windows they only rebuild modules that ship prebuilt (node-pty, koffi). Left out as unused by a coding agent
  (listed under `trimmed`): the bundled LibreOffice (office skill), speech recognition, the DevTools inspector and the web UI
  document preview, together 245 MB.

When an agent is missing, the agent list shows «не установлен» and points to Settings → Components.

## How it fits together

```text
page (text.js + agent.js) --/api/agents/send (NDJSON)--> bridge --> AgentManager
AgentManager --HTTP + /global/event--> opencode serve (owned process, port 8294)
AgentManager --JSON lines on stdin/stdout--> aider_bridge.py in Aider's Python (owned process, one per task)
AgentManager --ACP (JSON-RPC on stdin/stdout)--> dsh --profile acp in Node.js (owned process, one per task)
agent --OpenAI chat API--> ModelGateway (port 8293) --GPU.claim('text') + Text busy--> private Ollama /v1 (port 8292)
```

- `App/server/agents/gateway.py`: the agent's only model endpoint. Each request needs the per-launch bearer token and passes the
  same GPU admission as a chat answer; while Image/Video/Music work the request waits (up to 30 min, reported on the page) instead
  of running out of video memory. Model names are short aliases (`m-<sha1>`), rewritten to the Ollama name.
- `App/server/agents/opencode.py`: starts `opencode serve` with an allow-listed environment (no tokens or keys: OpenCode exposes
  provider keys it finds through its API), XDG folders under `UserData/Cache/Agents/opencode`, inline config with only the
  `visualforge` provider, `OPENCODE_DISABLE_PROJECT_CONFIG` (a repository cannot loosen permissions), no update checks, model
  catalog fetches, LSP downloads, sharing or web tools.
- `App/server/agents/aider.py` + `aider_bridge.py`: Aider has no server, so each task starts the bridge (`python -I -X utf8`, same
  allow-listed environment) and passes the folder, level, gateway URL/token and the last 20 messages of the chat over stdin. The
  bridge uses Aider's Python API (`Coder.create`, `diff` edit format, `ask` format for the read level) and:
  - asks before applying an answer's edits (one card with the SEARCH/REPLACE blocks as a diff), refuses files outside the folder,
    reports each applied edit as a step with its real diff;
  - answers Aider's own yes/no questions (add a mentioned file, create a file, run a shell command, add command output) by the
    level and the user's replies; runs approved commands itself without a console (10 min limit, output as a step), `git push`
    refused;
  - lets the process connect only to the gateway (plus its own loopback sockets, which asyncio needs on Windows); every other
    connection or name lookup is refused and logged. LiteLLM uses its bundled price list (`LITELLM_LOCAL_MODEL_COST_MAP`), the
    model's context comes from the studio, Aider's caches (`Path.home()/.aider`, repo-map tags) live in
    `UserData/Cache/Agents/aider`, nothing `.aider*` is written into the project;
  - commits only at the `auto` level, with the user's git identity (no "(aider)" names, no co-author lines); dirty files are never
    auto-committed; at the `ask` level edits stay uncommitted for review;
  - sends requests with reasoning off (`reasoning_effort: none`) and without Aider's temperature 0, so the model's own sampling
    applies. With temperature 0 Gemma 4 A4B reasoned in a loop until its `num_predict` (8192) and never answered, and LiteLLM
    drops Ollama's `reasoning` field, so nothing was visible meanwhile. OpenCode keeps reasoning;
  - works with the project folder as current directory (Aider's `subtree_only` is relative to it) and reads the studio's replies
    from a private duplicate of the stdin pipe, the process's standard input being NUL: a pending read on the stdin pipe made a
    DLL load in another thread hang (numpy/OpenBLAS for the repo map).
- `App/server/agents/dsh.py`: each task starts `node --require dsh_net_guard.cjs dsh/lib/bin.js --profile acp --patch
  <state>/task/patch.json` in the project folder and speaks the Agent Client Protocol: initialize, session/resume (the chat keeps
  dsh's session id; `DSH_HOME` is `UserData/Cache/Agents/dsh/home`) or session/new, session/prompt, session/cancel to stop.
  session/update becomes page events (dsh sends committed messages, not token deltas; each `usage_update` counts one model call
  and gives the context fill; dsh reports no output-token count). The patch (JSON, which dsh reads as YAML):
  - switches off the DeepSeek account, cloud model routes, web search/fetch, telemetry export (`otel`,
    `session-telemetry-otel`: the shipped profile sends feedback telemetry to `dsh-otel-collector.deepseeksvc.com`) and the
    subagent/workflow tools;
  - routes the only provider (`llm-pi-ai`, `openai-completions`) to the gateway with the alias, context and output limit, the
    key read from `VF_GATEWAY_TOKEN`; adds the chat's language instruction to the persona;
  - keeps dsh's own Windows sandbox off (`danger-full-access`, with a `visualforge` permission preset pairing it with approval
    `ask`, since the shipped presets pair full access with `never` and dsh refuses a composition that matches no preset).
    Commands run unconfined, as with OpenCode and Aider. Reason: for `workspace-write` that sandbox (a write-restricted,
    Low-integrity token) edits the project folder's security descriptor and, by design, never reverts it (its reuse cache;
    `icacls` cannot remove it, and its repair script keeps the deny and the label): an allow ACE for a per-folder `S-1-4-…`
    capability SID, a deny of "Delete subfolders and files" for Everyone inherited by subfolders, and a Low integrity label.
    Observed in the live check: ordinary deletes kept working, but a FullControl open of a subfolder is refused and
    Low-integrity processes may write into the folder. The owner chose to keep project folders untouched; the cost is that an
    approved command (a script the model wrote, tests, git hooks) can write outside the project;
  - inserts `dsh_policy.mjs`, a `tools/pre-execute` listener that applies the studio's level to every tool call: reads only
    inside the folder, edits and commands ask (the `auto` level: edits and git status/diff/log/add/commit pass), `git push`
    and files outside the folder are refused with a `VisualForge:` reason the page shows as «Запрещено правами агента».
    Without it dsh would edit and run commands without asking. dsh offers one-shot answers only;
    «Разрешать всегда» is repeated by the studio for the rest of the task.
  - `dsh_net_guard.cjs` (preloaded) lets Node connect only to the gateway and its own loopback listeners, binds any listener to
    127.0.0.1 and logs every refused connection or name lookup.
- `App/server/agents/manager.py`: one agent task at a time; translates OpenCode events (`message.part.*`, `permission.*`,
  `session.*`) into page events, relays the bridge's events; stores the request, answer, steps and errors in the chat file
  (`kind: "agent"`). A chat belongs to one agent; choosing another agent starts a new chat with the next task.
- Permission levels (per chat): `read` (no edits or commands), `ask` (default: every edit and command asks), `auto` (edits and
  git status/diff/log/add/commit without asking). `git push`, folders outside the project and web access are denied in every
  level. OpenCode gets them as a session ruleset, Aider through the bridge, dsh through its policy plugin (read from every task's
  patch, so a changed level applies to the resumed dsh session).

Settings: `UserData/Settings/agents.settings.json` (last agent, folder, level). Ports: `AgentGatewayPort`, `OpenCodePort` in
`runtime.settings.json` or `VISUALFORGE_AGENT_GATEWAY_PORT`, `VISUALFORGE_OPENCODE_PORT`. Logs: `UserData/Logs/agent-opencode.log`,
`UserData/Logs/agent-aider.log`, `UserData/Logs/agent-dsh.log`.

## Limits

- The agent's shell commands run as the user, with the user's profile (git identity). The folder rule confines the agents' own
  file tools; a command can still reach other paths. Use «Спрашивать» for unfamiliar tasks.
- Local models are weaker agents than hosted ones; a 32K context fills quickly with tool output. Aider shows its edit blocks in
  the answer text; a model that does not follow the SEARCH/REPLACE format gets Aider's own retry (up to 3 reflections).
- «Разрешать всегда» lasts for the current task with Aider and dsh (each task is a new process) and for the session with OpenCode.
- dsh is a developer preview (its SAFETY.md: no security audit, breaking changes expected).
- No images are sent to the agent yet.

## Tests

`tests/test_agents.py` (CPU only): gateway token/host/alias/Busy/Stop; chat storage; environment scrubbing; with the real
OpenCode against `tests/fake_openai.py`: permission → command → saved answer, `read` level and `git push` denial, rejection,
waiting for the GPU; with the real Aider: edit asked, applied and left uncommitted, `auto` commit with the user's identity,
rejected edit and `read` level, command approval, push and outside-file refusal, stop with a partial answer, history of the
chat in the next task, a file the model asks for is added and sent; with the real dsh: edit asked and applied, `auto` running git status
without asking and leaving the folder's ACL inherited only, read level / push / outside file refused, rejection, session resumed by the next task, stop; the catalog
entries install the agents exactly where the registry looks for them.

Live check (2026-10-27, Gemma 4 A4B 32K on the GPU, throwaway repository, level `ask`, requests answered by a fixed policy):
OpenCode fixed the bug, ran the tests and committed in 78 s (8 model calls); Aider did the same in 30 s (1 model call, 204
output tokens, one card for the edit and one for `python -m unittest` + `git add` + `git commit`); dsh did the same in 62 s
(with dsh's sandbox still on; 10 model calls, context 6.3K of 32K, reasoning visible; two of its three `edit` calls failed first — an escalation
`justification` argument without `sandbox_permissions`, then an `old_string` mismatch — and the model corrected itself). All
three answered in Russian; dsh's answer had one Ukrainian word (the model's). No refused network connection was logged.
