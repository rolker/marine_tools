---
issue: 75
---

# Issue #75 — Publish raw framing bytes on ~/raw topic from sound_speed_bridge

## Issue Review
**Status**: complete
**When**: 2026-07-28 00:00 +0000
**By**: Claude Code Agent (Claude Sonnet)

**Issue**: #75
**Comment**: (best-effort post follows this entry; not recorded inline)
**Scope verdict**: well-scoped

### Actions
- [ ] Settle message type in plan: `std_msgs/UInt8MultiArray` (no header/stamp) vs. a new stamped `marine_interfaces` message — if a new msg is introduced, `package.xml`, CMakeLists.txt, and any docs must be updated in the same PR (ADR-0008).
- [ ] Tests must cover both parse-success and parse-failure paths for the raw publisher, per issue request and marine_tools quality standard.

## Plan Authored
**Status**: complete
**When**: 2026-07-28 12:00 +0000
**By**: Claude Code Agent (Claude Sonnet)

**Plan**: `.agent/work-plans/issue-75/plan.md` at `a7fabb8`
**Branch**: feature/issue-75 at `a7fabb8`
**Phases**: single

### Open questions
- [ ] No open questions — plan is review-plan-ready.
