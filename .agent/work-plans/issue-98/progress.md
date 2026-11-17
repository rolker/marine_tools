---
issue: 98
---

# Issue #98 — bag_to_xtf: split the output by size, duration, time gap, and by straight line versus turn

## Issue Review
**Status**: complete
**When**: 2026-11-17 09:04 -05:00
**By**: Claude Sonnet

**Issue**: #98
**Comment**: (best-effort post follows this entry; not recorded inline)
**Scope verdict**: well-scoped

Owner decisions taken as given: one issue, two PRs (PR 1 size/duration/gap; PR 2 line/turn); automatic splitting, 500 MB default, option to disable; turn pings to separate files with a drop option; new file at a time gap.

Code read: `bag_analysis/cli/bag_to_xtf.py` (`_convert`, `main`, `_report`), `xtf/writer.py`. No `.agents/README.md` in the repo (gap noted, not for this issue).

### Findings for the plan
- `_convert` builds one `XtfWriter` on one stream, and `ping_number` comes from `writer.ping_count`. Splitting needs a per-part writer while the ping number stays global. `main` owns the single `.partial` and rename, so that moves into a part manager.
- Size is only known after a ping is written. The writer should report the exact byte size of the next ping (header plus channel data) so a part never exceeds the limit. Do not use `stream.tell()` after the fact.
- `prev_emitted` drives `speed` and `seconds_per_ping`. At a part boundary decide whether it carries over. Carrying it over keeps the content identical to the unsplit file, which the acceptance criterion requires ("same content"). Note that this makes a part's first ping depend on the previous part.
- Time-gap threshold: the existing `_MAX_SECONDS_PER_PING` (2 s) is for the ping-interval field and is not a gap threshold. Use a separate option with its own default (suggest 60 s; confirm against real bags).
- Failure mid-run: the issue asks to decide what happens to finished parts. Recommend keeping completed parts and reporting them with a non-zero exit, so a long run's finished work is not thrown away. Delete only the in-progress `.partial`.
- A one-part run: recommend the plain `--output` name, so existing scripts keep working. The time suffix applies only when more than one part is written. This means the first part must be renamed once it is known that a second exists, or the name is fixed at finalize time. Flag this for the plan.
- `--max-pings` and `--start-time`/`--end-time` must keep working with splitting. Combine them with the part counts.
- Zero-ping case (`ping_count == 0`, exit 1) must still hold when no part is produced.
- `.partial` cleanup on `BaseException` must cover every part, not just one.
- Part-name collision: two parts with a first ping in the same UTC second. The time format has one-second resolution. Needs a rule (fractional seconds or a counter suffix).
- Existing output files from an earlier run: define overwrite behaviour for parts (stale parts from a previous run with a different split would otherwise sit beside the new ones).
- PR 2: the line/turn pass needs positions before writing. Pings carry full sample arrays, so the plan must not hold them all in memory. Recommend a first pass that runs only TF and pairing and keeps just (t, ecef, slant_range) per ping, then a second pass over the bag for writing, with the first-pass keep/class decision applied by ping index. Both passes must use the same pairing and trimming code, so refactor `_convert` into a ping generator in PR 1 to keep this open.
- PR 2 turn parameters are the main risk. Defaults must be measured on real bags, including 0.5 m/s wandering tracks. Make the measurement an explicit task, not a guess.
- Both `--layout` values: the layout is passed to `XtfWriter` per part, so the plan needs a test of each.

### Principle / ADR check
- Workspace separation and repo placement: OK, belongs in marine_tools.
- Robustness and error handling: Action needed. Mid-run failure and a partly written part set must be defined and tested, not left implicit.
- Tests with the change: Action needed. The acceptance list is good; add the name-collision, overwrite and `--max-pings` cases.
- Docs: Action needed. Update the `bag_to_xtf` README section (acceptance already says so) and the CLI help.
- No ADR is triggered. `.agents/README.md` is missing in this repo, which is a separate task.

### Actions
- [ ] Define and test the failure behaviour for finished parts (recommend keep and report, non-zero exit).
- [ ] Decide single-part naming (recommend plain `--output` name when only one part).
- [ ] Specify that splitting is by exact next-ping size, and that ping_number and the prev_emitted carry-over stay global so parts equal the unsplit content.
- [ ] Add a separate time-gap threshold option and default; define part-name collision and overwrite-existing rules.
- [ ] In PR 1, refactor `_convert` into a ping generator so PR 2's two-pass design fits.
- [ ] For PR 2, plan a measured-defaults task on real bags (0.5 m/s tracks included) and a nav-only first pass that does not hold sample data in memory.
- [ ] Update the README section and CLI help; cover both layouts in tests.

## Plan Authored
**Status**: complete
**When**: 2026-11-17 09:05 -05:00
**By**: Claude Sonnet

**Plan**: `.agent/work-plans/issue-98/plan.md` at `7bc6f49`
**Branch**: feature/issue-98 at `7bc6f49`
**Phases**: single (PR 1 only; PR 2 line/turn gets its own plan)

### Open questions
- [ ] Is deleting stale generated parts from an earlier run on success acceptable (alternative: refuse to run)?
- [ ] Option names (--max-part-size-mb, --max-part-duration-min, --gap-split-s, --no-split) and MB = 10^6 bytes.
- [ ] Confirm the 60 s gap default against real bags during implementation.
