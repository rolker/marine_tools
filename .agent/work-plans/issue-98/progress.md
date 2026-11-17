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

## Plan Review
**Status**: complete
**When**: 2026-11-17 09:07 -05:00
**By**: Claude Sonnet

**Plan**: `.agent/work-plans/issue-98/plan.md` at `7bc6f49`
**PR**: PR-less
**Verdict**: changes-requested

Read cold as a dispatched sub-agent (handoff header present); independent of the plan author.

### Findings
- [ ] (must-fix) Stale-part deletion on success removes files this run did not write, matched by name only. A quick `--max-pings` or `--start-time` test run to the same `--output` would delete a full multi-GB export (step 9 itself does this kind of run), and a single-part run deletes earlier parts too. Replace with: list stale parts and plain-name file in a loud end-of-run warning, exit 0, delete nothing; offer an explicit opt-in flag (e.g. `--remove-stale-parts`) if wanted. If deletion stays, use `re.escape(stem)` and `fullmatch`, and test a stem with regex metacharacters. Update the Open Question and the stale-part test accordingly — `plan.md:24`, `plan.md:45`, `plan.md:87`
- [ ] (suggestion) Generator refactor pitfall: the original clears `pending` after `emit`, then checks `--max-pings`. If the generator yields before clearing `pending`, an early `close()` counts the just-yielded pair as `unpaired` and the report counts differ from today. Clear `pending` before the yield, and keep the `paired`/`no_altitude` counting on one side only. Capture the "byte-identical" golden output from current main before refactoring (commit the characterization test first) — `plan.md:15`
- [ ] (suggestion) Report omits the per-part time span the issue asks for ("file, ping count, time span, size"); add first/last ping UTC — `plan.md:30`
- [ ] (suggestion) Option names differ from the issue's `--split-size` / `--split-duration`; the plan flags them as its choice. Either use the issue names or register them as aliases, and say so in the PR — `plan.md:26`
- [ ] (suggestion) Acceptance says each part opens in pyxtf on its own; neither the tests nor step 9 check it. Add a `pytest.importorskip('pyxtf')` read of every part in the split tests (the writer test already does this for one file) and a pyxtf read in step 9 — `plan.md:36`, `plan.md:32`
- [ ] (suggestion) Naming: opening every part's `.partial` lazily under its own first-ping time name, and renaming to the plain name only at the end of a one-part run, removes the special case where part 1 streams to `<output>.partial` and is renamed at rollover. Either way, leftover `.partial` files from a killed run no longer get overwritten by the next run (names now vary) — mention them in the stale warning — `plan.md:22`
- [ ] (suggestion) Tests for carry-over are vacuous on a stationary or evenly zero-speed synthetic track. Specify moving positions and ping spacing under `_MAX_SECONDS_PER_PING` (e.g. 0.1–0.5 s), with a split falling mid-run, and confirm the test fails if `prev_emitted` is reset at a boundary — `plan.md:38`
- [ ] (suggestion) Option interplay and validation are unspecified: `--no-split` together with an explicit limit, negative values, a size limit smaller than header plus one ping (every ping its own part). Also warn when the size limit exceeds 2^31 bytes, since the issue's motivation is the 32-bit offset — `plan.md:25-29`
- [ ] (suggestion) Compute `ping_size` from `np.asarray(samples).size * 2` rather than building the byte arrays twice per ping; keep the shared constant arithmetic so it cannot drift from `write_ping` — `plan.md:16`
- [ ] (suggestion) Compare gap/duration in integer `t_ns` rather than the microsecond-truncated datetimes, and state that "previous ping" is the previous emitted ping across parts — `plan.md:17`
- [ ] (suggestion) After a gap split, the first ping of the new part carries a speed averaged over the gap (distance over a 60+ s sonar-off interval), which is junk, though identical to today's unsplit file. Keeping it satisfies "same content"; note it in the README or decide deliberately — `plan.md:17`

### Answers to the four review points
1. Deleting unwritten files: not safe enough (see must-fix). Name-only provenance cannot tell this bag's stale parts from another export. Warn plus opt-in removal is better than both deleting and refusing to run.
2. Part 1 rename: no failure window of consequence. Rollover happens with a next ping in hand and part 1 already closed, so the renamed file is complete. Surprises are only leftover `.partial` names (above) and a failed open of part 2 after part 1 was renamed (acceptable: part 1 is finished).
3. Size guarantee: holds in both layouts. Total bytes are layout-independent (256 + 2x64 + 2 bytes per sample on both channels, 1024 header), the writer is the single place that writes, and the only exception (a lone oversized ping) is specified. Needs the writer-length test already planned, plus the option validation above.
4. Global state: testable, and the byte comparison is the right test (file header carries no count or time, so part[1024:] concatenated equals single[1024:]). It is only meaningful with the moving-track data described above.

### Dimension verdicts
Scope Good (one PR, 5 files). Issue alignment Needs work (time span, option names, pyxtf). File targeting Good (`contacts_to_geojson` imports `_lookup_pose`/`_stamp_ns`, untouched). Consequences Good. Docs impact Good. Principle alignment Needs work (stale deletion, error handling). ADR none triggered. ROS conventions N/A.

## Implementation
**Status**: complete
**When**: 2026-11-17 09:25 -05:00
**By**: Claude Sonnet

**Plan**: `.agent/work-plans/issue-98/plan.md` (amended for the owner decisions and the plan-review findings)
**Branch**: feature/issue-98 (local only, not pushed, no PR)

### Done
- Plan amended first (stale files: warn only, option names `--split-size` / `--split-duration` / `--split-gap` / `--no-split`, all review suggestions).
- Golden-file test captured from the pre-refactor code, then `_iter_pings` generator refactor with the golden test unchanged.
- `XtfWriter.ping_size` / `bytes_written`; `_PartSink` with lazy per-part naming, one-part rename to the plain name, failure handling, option validation, per-part report with time span, end-of-run warning for old files (nothing deleted).
- README section and module docstring.
- Tests: 127 run in bag_analysis, 0 failures, 2 skipped (pyxtf not installed here, so the pyxtf read tests skip).
- Real data: bizzyboat_sonar 2026-08-25T13-59-09, whole bag: 5 parts, all <= 500 MB, parts' ping bytes equal the --no-split run (sha256). Largest gap in three bags 7.5 s; one 1037.7 s gap in the 2026-08-28 bag split correctly. 60 s default kept.

### Deviations
See "Implementation Notes (deviations)" in the plan: golden test uses default limits, `--max-pings` below 1 rejected, sink/options/report/old-file warning in one commit.

### Not verified
- pyxtf reading of the parts (pyxtf not installed on this host).

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-17 09:30 -05:00
**By**: Claude Code Agent (Claude Sonnet)
**Verdict**: changes-requested

**Branch**: feature/issue-98 at `f6b9840`
**Mode**: pre-push
**Depth**: Standard (reason: 1248 lines, survey-data writer, owner-requested)
**Must-fix**: 1 | **Suggestions**: 6
**Round**: 1 | **Ship**: continue — one real data-loss path (earlier same-named part silently replaced), 4-way confirmed; fix then re-review narrowly

### Findings
- [x] (must-fix) a rerun whose first ping gives an existing part name silently replaces that file (end_part replace, then finish moves it), contradicting "nothing was deleted"; check the disk in _final_path (reserve final and .partial names) or warn about replaced files, and test it — `bag_analysis/bag_analysis/cli/bag_to_xtf.py:523`
- [x] (suggestion) zero-ping failure returns before _warn_old_files, so a stale plain output goes unmentioned — `bag_to_xtf.py:673`
- [x] (suggestion) inf (and 1e308) for --split-size/--split-duration/--split-gap raises uncaught OverflowError; reject non-finite via parser.error; also tiny --split-duration truncates to 0 ns and splits every ping — `bag_to_xtf.py:194`
- [x] (suggestion) abort()/_warn_old_files can raise inside the except handler and hide the original error and the finished-parts report; guard with try/except OSError — `bag_to_xtf.py:568`
- [x] (suggestion) interrupt between replace and parts.append (end_part) or between replace and path assignment (finish) leaves a complete part unreported and later called an old file; also no fsync before rename — `bag_to_xtf.py:545`
- [x] (suggestion) --output that is a directory is only detected after the whole bag is converted; check up front — `bag_to_xtf.py:650`
- [x] (suggestion) failure report does not say how many pings were in the discarded .partial — `bag_to_xtf.py:667`

## Implementation
**Status**: complete
**When**: 2026-11-17 10:16 -05:00
**By**: Claude Sonnet

**Branch**: feature/issue-98 at `7d329cc` (local only, not pushed, no PR)
**Addressed**: Local Review (Pre-Push) round 1, 2026-11-17 09:30 -05:00, at `f6b9840` (1 must-fix, 6 suggestions)
**Commits**: `1c2eca3` (code and tests), `7d329cc` (README and plan)

### Actions
- [x] (must-fix) rerun silently replacing a part: owner decision REFUSE UNLESS `--overwrite`; up-front scan, exclusive `.partial` open, no-replace move incl. the plain-name rename, replaced files reported, unregenerated old files warned ("mixes files from two runs") — `bag_to_xtf.py` `_preflight`, `_PartSink`
- [x] zero-ping exit now runs the old-file warning
- [x] non-finite, absurd and 0 ns option values rejected (exit 2)
- [x] cleanup is best effort and cannot hide the original error or the parts report
- [x] interrupt windows reconciled against the disk; parts fsynced before rename
- [x] `--output` directory / unwritable folder refused up front
- [x] failure report gives the ping count of the discarded `.partial`

### Notes
- Refuse path: exit 2, stderr `error: N file(s) for this output already exist in <folder>:` + up to 10 names + `... and K more (N files in all)` + `Nothing was written. Move them away, choose another --output, or pass --overwrite ...`.
- A one-part `--overwrite` run passes through its time name, so an old file of that time name is replaced and listed (documented).
- Plan and README updated; the superseded accepted deviation is rewritten in the plan.
- Tests: 153 run in bag_analysis, 0 failures, 2 skipped (pyxtf not installed).


## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-17 10:50 -05:00
**By**: Claude Sonnet
**Verdict**: changes-requested

**Branch**: feature/issue-98 at `29f24d5`
**Mode**: pre-push (round 2, delta since `5f5d59f`)
**Depth**: Standard (reason: survey-data writer, file-replacement logic; owner-requested)
**Specialists**: manual static/adversarial read (Lens A + B), cross-model (gemini: no findings; codex: 2 must-fix), probe scripts. Tests 153 passed, 2 skipped.
**Must-fix**: 1 | **Suggestions**: 4
**Round**: 2 | **Ship**: continue — one cheap, real truncate-a-foreign-file path under `--overwrite` (confirmed by a probe and by codex); fix, then a narrow re-check

### Round 1 findings
All seven closed as claimed (refuse unless `--overwrite`, exclusive `.partial`, no-replace move, zero-ping warning, option validation, best-effort cleanup, interrupt reconciliation plus fsync, directory/unwritable refusal, discarded ping count). Commit `1c2eca3` touched only `bag_to_xtf.py` and its tests; nothing unrelated.

### Findings
- [ ] (must-fix) `--overwrite` opens an existing `.partial` with `'wb'`, which follows a symlink and truncates a hard-linked inode: the target file (outside the run's names) is emptied, while the report lists only the `.partial` path as "replaced". Probe: symlink `a_...Z.xtf.partial -> target`, target became empty. Unlink the old `.partial` (record it as replaced) and then open with `'xb'`; add symlink and hard-link tests — `bag_analysis/bag_analysis/cli/bag_to_xtf.py:627-631` (cross-model confirmed, codex)
- [ ] (suggestion) hard-link fallback (`os.link` failing with EPERM/ENOTSUP, as on FAT and some SMB shares) is check-then-`os.replace`: a file appearing in between is replaced silently, yet the README and docstring say "nothing is ever replaced, even if a file appears". Either say so in the README for such filesystems or fall back to an exclusive create plus copy; no test covers the fallback at all — `bag_to_xtf.py:657-667`, `README.md` ("Without `--overwrite` nothing is ever replaced") (codex rated must-fix; kept as suggestion because the window is microseconds and the preflight already refused every pre-existing name)
- [ ] (suggestion) `os.fsync(stream.fileno())` is unguarded: a filesystem that rejects file fsync (EINVAL/ENOSYS/ENOTSUP, some FUSE/SMB mounts) fails the run after a complete part was written, and `abort()` then deletes that part's `.partial`. Tolerate those errnos (keep EIO fatal), unlike `_fsync_dir` which already is best effort — `bag_to_xtf.py:676`
- [ ] (suggestion) on the success path `_warn_old_files` (and `_report`) are not wrapped like they are on the failure path; an `iterdir` error on a dropped NAS mount after a complete conversion gives a traceback and a non-zero exit — `bag_to_xtf.py:914,918`
- [ ] (suggestion) when the final-name move meets a file that appeared mid-run (`FileExistsError`), the finished part's `.partial` is then deleted by `abort()`: the new data is lost to protect the foreign file. Keep the `.partial` and name it in the report — `bag_to_xtf.py:685-696`

### Answers to the seven points
1. Without `--overwrite`: `.partial` is `xb` (not following symlinks), the move is link-then-unlink, so no replacement except the no-hard-link fallback race above (not reported silently, but unprotected). With `--overwrite`: the symlink/hard-link truncation above is the one unreported path. The probe file uses `TemporaryFile`, never a named file. One-part pass through the time name replaces an old file of that name, and it is reported and documented.
2. Pattern `<stem>_\d{8}T\d{6}Z(_\d+)?<suffix>(.partial)?` plus plain name and plain `.partial`, `re.escape`d, `fullmatch`: not too wide. Misses only case variants on case-insensitive filesystems (mid-run guard still protects without `--overwrite`).
3. Yes: `TemporaryFile` is unlinked on creation or uses `O_TMPFILE` and is random-named with `O_EXCL`; it cannot collide.
4. Directory fsync is safe (open or fsync errors are swallowed). File fsync is not (see suggestion 3).
5. Caps (10 TB, 10^7 minutes, 10^9 s) reject nothing legitimate; `0` and `--no-split` cover "off".
6. No, `1c2eca3` changed only this feature's code and tests.
7. README matches the code for options, defaults, exit 2 on refusal, reporting and the up-front folder checks; the one inaccuracy is "nothing is ever replaced" (suggestion 1). Minor: the README does not list `--overwrite` in the options table and says nothing of `--max-pings` below 1 being rejected.
