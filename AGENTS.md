# AGENTS.md — marine_tools

Instructions for AI agents working in this repository — including **GitHub
Copilot code review**, which reads this file when reviewing PRs. There is no
`.agents/README.md` deep guide yet (the workspace convention for project
agent guides — distinct from the `.agent/work-plans/` directory); start from
the top-level `README.md` and per-package READMEs.

## Workspace Rules

This repo is developed inside a
[ROS 2 Agent Workspace](https://github.com/rolker/ros2_agent_workspace).
The workspace root `AGENTS.md` carries the full shared rules (worktree
isolation, issue-first policy, commit conventions, AI signatures). This file
**references** those rules and adds repo-specific context only — it must
never restate or fork them.

## Quality Standard

This is software for autonomous robot boats operating on open water.
Robustness is not optional.

- Fix bugs completely: add the test, handle the edge case, check the
  lifecycle transition.
- Concerns about error handling, silent failures, stale data, or missing
  validation are not nits — flag them unless the failure mode genuinely
  cannot occur. "Config is under our control" and "pathological input" are
  not blanket dismissals; field configs change under pressure.
- A change includes its consequences: tests, documentation, and dependent
  references update in the same PR.

## Reviewing PRs

- If the PR carries a work plan (`.agent/work-plans/issue-<N>/plan.md` or a
  plan in the PR body), the plan is kept **in sync with the implementation
  as it evolves** — an implementation that matches the current plan text is
  not "plan drift", even if the plan changed after the PR opened.
- Verify claims against source: parameters, topics, services, and message
  types in docs must match the code.

## Review Context — marine_tools

- **Sensor bridges and survey tooling**: drivers here speak vendor and
  reverse-engineered wire protocols (Garmin GCV sidescan, Kongsberg EM
  datagrams, ZDA/sound-speed serial). Byte-level protocol changes need
  capture or bag evidence — for the Garmin protocol, `gcv_protocol.md` in
  this repo is the authoritative reference.
- **Driver conventions** (learned the hard way): mounting/orientation
  belongs in TF/`frame_id`, not parameters; no vendor trademarks in names
  or topics; shared-repo drivers ship *generic* launch files and node
  defaults — platform-specific wiring lives in the platform repos.
- **Don't invent message-field semantics**: read the `.msg` definition and
  precedent drivers before mapping protocol fields onto messages.
- **`bag_analysis` is the standard deployment-analysis path**
  (`bag_to_sqlite` → report); its output schema feeds downstream analysis
  scripts, so schema changes ripple beyond this repo.
