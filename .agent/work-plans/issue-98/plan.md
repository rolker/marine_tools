# Plan: bag_to_xtf: split the output by size, duration, time gap, and by straight line versus turn (PR 1 of 2)

## Issue

https://github.com/rolker/marine_tools/issues/98

## Context

`bag_to_xtf` writes one XTF per bag. Appledore exports run 0.09 to 3.5 GB per bag (four of eight over 2 GB), too big for some receiving tools. This plan is **PR 1 only**: split by size, by duration and at a time gap. PR 2 (straight line versus turn) gets its own plan. The PR says "Part of #98", not a closing keyword.

Today `main` opens one `.partial`, `_convert` builds one `XtfWriter` on it, takes `ping_number` from `writer.ping_count`, and `main` renames at the end.

Owner decisions (Roland, final) folded in below: **the run refuses to start if the plain output or any file of this output's part-name pattern exists, unless `--overwrite` (step 6)**; files the run did not regenerate are warned about and never deleted; option names are `--split-size`, `--split-duration`, `--split-gap`, `--no-split`; every plan-review suggestion is adopted.

## Approach

1. **Golden output first, then the ping generator (also prepares PR 2).** Before touching `_convert`, commit a characterization test that builds a synthetic bag stream (fake `iter_messages`, fake TF), runs today's code and compares the whole output file to a golden file stored in the test directory, together with the report counts. Then split `_convert` into `_iter_pings(args, counts)`, a generator yielding `(port, stbd, altitude)` for each paired ping. It owns TF, nadir, window trim, pairing and the `unpaired` count. The write side is separate. The generator clears the pending pair **before** it yields, so an early `close()` (from `--max-pings`) does not count the just-yielded pair as `unpaired`; the `paired` / `no_altitude` counts are kept on the consumer side only. The golden test must pass unchanged after the refactor, and a `--max-pings` case pins the counts. PR 2 can later run the generator twice (a nav-only pass, then the writing pass) without touching the pairing code.
2. **Exact next-ping size.** `XtfWriter` gets `ping_size(port, starboard)` and a `bytes_written` property (starts at 1024 for the header, grows by what `write_ping` writes). `ping_size` is computed from sample counts (`np.asarray(samples).size * 2`), not by building the byte arrays; `write_ping` and `ping_size` share one helper for the arithmetic (256 + 2 x 64 + 2 bytes per sample on both channels) so they cannot drift. No `stream.tell()`.
3. **Part sink** (`_PartSink` in `bag_to_xtf.py`). Holds the current part's `.partial` path, stream, writer, first-ping and last-ping time, and ping count. `ping_number` and `prev_emitted` (speed, seconds-per-ping) stay in the emit loop, outside the sink, so they are global and the concatenated parts hold exactly the pings one file would. "Previous ping" for the gap rule is the previous **emitted** ping, across parts. Before each ping the loop asks whether to roll over; if so it closes the current part and opens the next. A new part starts when any of:
   - `bytes_written + ping_size > max_part_bytes` and the part holds at least one ping (one oversized ping gets a part alone);
   - `t_ns - part_first_t_ns >= max_duration_ns` (only if set);
   - `t_ns - prev_emitted_t_ns > gap_ns`.
   All comparisons are in integer nanoseconds (`t_ns`), not the microsecond-truncated datetimes. `--max-pings` counts pings across all parts, checked in the loop as today.
4. **Naming (lazy).** A part's `.partial` is opened only when its first ping is in hand, and is named after that ping: `<stem>_<first-ping UTC YYYYmmddTHHMMSSZ><suffix>.partial`. Time is the ping stamp (not wall clock), UTC. Two parts whose first ping falls in the same UTC second get `_2`, `_3`... before the suffix (checked against names used this run). When a part is finished it is renamed to its final time name. At the end of a successful run that has exactly one part, that part is renamed to the plain `--output`, so existing scripts keep working. With two or more parts there is no plain-name file from this run. Because names vary, a killed run's `.partial` files are no longer overwritten by the next run; the end-of-run warning lists them (step 6).
5. **Failure.** Finished parts stay. On any `BaseException` only the current `.partial` is deleted; every finished part keeps its time name (a failed run is never a "single part" run, so nothing is renamed to the plain name). `main` prints the finished-part list and returns non-zero for a handled error; for an exception it prints the list then re-raises. Zero pings overall still returns 1 with no files.
6. **Existing files: refuse unless `--overwrite` (owner decision, replaces the earlier "rerun replaces same-named part" rule).** Before the bag is read (`_preflight`): `--output` that is a directory, or a folder that cannot be written (probe file), is exit 2. Then scan the folder for the plain `--output`, `<name>.partial`, and files matching `re.escape(stem)` + `_\d{8}T\d{6}Z(_\d+)?` + `re.escape(suffix)` (`fullmatch`, optional `.partial`). If any exist and `--overwrite` is not set: print an error naming them (first 10, then "and N more" with the total) and exit 2, writing nothing. This deliberately changes the single-file behaviour too (the plain output used to be replaced silently); the README says so.
   - With `--overwrite`: the run may replace files whose names it produces, and the end-of-run report lists each replaced file. A one-part run passes through its time name before taking the plain name, so an old file of that time name is replaced (and listed). Old files of the pattern that were not regenerated are warned about and never deleted; the warning says the folder now mixes two runs. Exit stays 0.
   - Fail-safe races: without `--overwrite`, `.partial` files are opened `'xb'` and a part is moved into place by hard link (fails if the name exists; rename after an existence check where links are unsupported), including the plain-name move of a one-part run. A foreign file that appears mid-run is never truncated or replaced; the run fails with the finished parts kept, listed.
   - Failure handling: the original error is printed first; cleanup (`abort`, report, old-file warning) is best effort and cannot hide it; the report says how many pings were in the discarded `.partial`. Parts are fsynced before rename. An interrupt around a rename is reconciled against the disk so a moved part is still reported.
   - Zero pings still returns 1 with no files, and still prints the old-file warning.
7. **Options** (all in `_build_parser`):
   - `--split-size <MB>` (float, default 500; 0 means no size limit). MB = 10^6 bytes, so a part never exceeds that.
   - `--split-duration <minutes>` (float, default off).
   - `--split-gap <seconds>` (float, default 60; 0 means off). Separate from `_MAX_SECONDS_PER_PING`, which is the ping-interval field's rule.
   - `--no-split` turns all three off and gives today's single file.
   - Validation (argparse `parser.error`, exit 2): negative values are rejected; `--no-split` together with any explicit limit is rejected (detected by defaults of `None` resolved after parsing); a size limit smaller than the header plus one minimum ping is not rejected (each ping becomes its own part, by design) but prints a warning at start; a size limit above 2^31 bytes prints a warning (the XTF 32-bit offset motivation).
   - Report: for each part print file name, ping count, first and last ping UTC (time span) and size, plus the reason each rollover happened (size, duration, gap).
8. **Docs.** README section for `bag_to_xtf` and `--help` text: options, naming rule, single part keeps the plain name, failure behaviour, the old-file warning, and a note that the first ping after a gap carries a speed averaged over the gap (distance over the sonar-off interval), exactly as the unsplit file does today, because the carry-over is kept global so the parts equal the single file. Update the module docstring (it says one `.partial` and one rename).
9. **Check on real data.** Convert one real Appledore bag (read only) with the default, with `--no-split` over the same window, and with a small size; output only under `/home/roland/project11/.agent/scratchpad/xtf_split/`. Check no part exceeds the limit, each part opens in pyxtf if importable, and the parts' ping bytes (after the 1024-byte header) concatenated equal the unsplit run's ping bytes (sha256). Look at real inter-ping gaps to confirm or move the 60 s default. Delete the large outputs afterwards, keep a short text record.

## Tests

Characterization test (step 1, before the refactor): golden file for the single-file output and the report counts, including a `--max-pings` case.

New `test_xtf_split.py` using a fake `iter_messages` (monkeypatch) and small synthetic pings. The synthetic track **moves** (ECEF position advancing each ping) with ping spacing 0.1 to 0.5 s (under `_MAX_SECONDS_PER_PING`), so speed and seconds-per-ping are non-trivial, with a split falling mid-run. Writer tests are added to `test_xtf_writer.py`.
- Writer: `ping_size` equals the bytes `write_ping` actually adds, in both layouts; `bytes_written` equals file length.
- Size split: no part exceeds the limit (assert on file sizes), oversized single ping gets its own part, concatenated ping bytes (after each 1024-byte header) equal the unsplit file's, `ping_number` runs continuously, first ping of part 2 carries the same speed and seconds-per-ping as in the unsplit file. The carry-over assertion must fail if `prev_emitted` is reset at a boundary (verify by temporarily resetting it). Both layouts.
- Every part is read with pyxtf under `pytest.importorskip('pyxtf')`: ping counts add up to the total.
- Duration split alone and combined with size (first reached wins).
- Gap split: gap longer than the threshold starts a new part; shorter does not; `--split-gap 0` and `--no-split` give one file. Gap is measured against the previous emitted ping, in integer nanoseconds (a case just under and just over).
- Naming: one part gets the plain name; more than one gets time names, no plain file, and no `.partial` left; same-second collision gives `_2`.
- Option validation: negatives, `--no-split` with a limit, tiny size limit warning, over-2^31 warning.
- `--max-pings` across parts; `--start-time`/`--end-time` trimming with splitting.
- Failure: exception mid-run in part 2 leaves finished parts with time names, no `.partial`; a failure in part 1 leaves nothing; exit code non-zero on a handled error.
- Zero pings: exit 1, no files.
- Existing files: a rerun without `--overwrite` refuses (exit 2) and leaves every file byte-identical, also for `--max-pings` over a full export, the plain-name single file and a `.partial` leftover; a long list is capped; with `--overwrite` it replaces and reports; when the old run had more parts the leftovers are warned about ("mixes files from two runs") and kept; a foreign file appearing mid-run as `.partial`, as a final name or as the plain name is not replaced; unrelated files are not listed; a stem with regex metacharacters matches only its own parts; nothing is deleted on success or failure.
- Options: `inf`, `nan`, `1e308`, and a duration or gap that rounds to 0 ns are rejected (exit 2); a directory or unwritable `--output` is refused before the bag is read.
- Failure: cleanup errors do not hide the original error or the parts report; the discarded `.partial`'s ping count is reported; an interrupt just after a rename still reports the part; parts are fsynced.

## Files to Change

| File | Change |
|------|--------|
| `bag_analysis/bag_analysis/cli/bag_to_xtf.py` | `_iter_pings` generator, `_PartSink`, split options and validation, lazy naming, failure handling, old-file warning, report |
| `bag_analysis/bag_analysis/xtf/writer.py` | `ping_size`, `bytes_written` |
| `bag_analysis/test/test_xtf_writer.py` | size tests |
| `bag_analysis/test/test_xtf_golden.py` (new), `test/data/xtf_golden_single.xtf`, `test/xtf_fakes.py` (shared synthetic bag stream) | characterization test and fake-bag helper |
| `bag_analysis/test/test_xtf_split.py` (new) | split tests above |
| `bag_analysis/README.md` | `bag_to_xtf` section |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Robustness / error handling | Failure and old-file rules are specified and tested; no silent partial sets; nothing is ever deleted, and nothing is replaced without `--overwrite` |
| Tests with the change | Both layouts, `--max-pings`, trimming, validation, moving-track carry-over covered |
| Workspace separation | Change lives in marine_tools only |
| Data of record | Real bags read only; test output under the scratchpad |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| None | No | Review found none triggered |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| `bag_to_xtf` output naming | README, `--help`, any script that reads `--output` | Yes (scripts keep working for one part; multi-part names documented) |
| `XtfWriter` | Other users of the writer, writer tests | Yes, additive API only |
| Conversion loop | PR 2 (line/turn) | Yes, generator is the hook |

## Documentation & Instruction Impact

- **Stale docs** (must land in this PR): `bag_analysis/README.md` `bag_to_xtf` section and the module docstring.
- **Agent-instruction candidates**: None. This repo has no `.agents/README.md`, a separate task.

## Open Questions

Resolved by the owner: stale files are warned about, never deleted; option names as above; MB = 10^6 bytes. Remaining:
- Is 60 s a good gap default? Confirm against real bags in step 9.

## Implementation Notes (deviations)

- The golden test runs with the default limits (a small run is one part) rather than `--no-split`, because the golden file was captured from code that had no such option.
- `--max-pings` below 1 is now rejected (exit 2). The old loop checked the cap only after a ping was processed, so `--max-pings 0` ended the run with no pings; the generator form would instead have converted everything.
- The sink, options, report and old-file warning landed in one commit because they share `main` and the report; the README is separate.
- Superseded (pre-push review round 1, owner decision): the first implementation let a rerun replace a same-named part silently. Now a rerun refuses unless `--overwrite` (step 6); with it, replaced files are listed and unregenerated old files are warned about.
- A start-up warning for a size limit below header-plus-minimal-ping (1408 bytes) is used, since the real ping size is not known at option-parsing time; a part that exceeds the limit because of one big ping is flagged in the report.
- Real-data check (step 9): 5 parts from the 4 GB bag of 2026-08-25, all at most 500 MB, parts' ping bytes equal the `--no-split` run (sha256). pyxtf is not installed here, so the pyxtf checks only ran as skipped tests. Gaps: largest 7.5 s in three bags, one 1037.7 s gap in the 2026-08-28 bag, so 60 s sits well between them and is kept.

## Estimated Scope

Single PR (PR 1). PR 2 is a separate plan: measured turn thresholds on real bags (including 0.5 m/s tracks), a nav-only first pass, drop-turn option.
