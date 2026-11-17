# Plan: bag_to_xtf: split the output by size, duration, time gap, and by straight line versus turn (PR 1 of 2)

## Issue

https://github.com/rolker/marine_tools/issues/98

## Context

`bag_to_xtf` writes one XTF per bag. Appledore exports run 0.09 to 3.5 GB per bag (four of eight over 2 GB), too big for some receiving tools. This plan is **PR 1 only**: split by size, by duration and at a time gap. PR 2 (straight line versus turn) gets its own plan. The PR says "Part of #98", not a closing keyword.

Today `main` opens one `.partial`, `_convert` builds one `XtfWriter` on it, takes `ping_number` from `writer.ping_count`, and `main` renames at the end.

## Approach

1. **Ping generator (also prepares PR 2).** Split `_convert` into `_iter_pings(args, counts)`, a generator yielding `(port, stbd, altitude)` for each paired ping. It owns TF, nadir, window trim, pairing and `--max-pings`-independent logic, and the `unpaired` count (in a `finally`, so an early `close()` still counts leftovers). The write side is separate. PR 2 can then run the generator twice (a nav-only pass, then the writing pass) without touching the pairing code. No behaviour change in this step; a test pins that the single-file output is byte-identical to before.
2. **Exact next-ping size.** `XtfWriter` gets `ping_size(port, starboard)` (256 + 2x64 + sample bytes, the same arithmetic `write_ping` uses, shared in one helper so they cannot drift) and a `bytes_written` property (starts at 1024 for the header). No `stream.tell()` after the fact.
3. **Part manager** (`_PartSink` in `bag_to_xtf.py`). Holds the current part's `.partial` path, stream, writer, first-ping time, last-ping time. `ping_number` and `prev_emitted` (speed, seconds-per-ping) stay in the emit loop, outside the sink, so they are global and the concatenated parts hold exactly what one file would. Before each ping, the loop asks whether to roll over; if so it closes the current part and opens the next. A new part starts when any of:
   - `bytes_written + ping_size > max_part_bytes` and the part holds at least one ping (one oversized ping gets a part alone);
   - `ping.time - part_first_time >= max_duration` (only if set);
   - `ping.time - previous_ping.time > gap_s`.
   `--max-pings` counts pings across all parts (global count, checked in the loop as today).
4. **Naming.** Part 1 streams to `<output>.partial` and its final name is undecided. If a second part is ever started, part 1 is renamed to `<stem>_<first-ping UTC YYYYmmddTHHMMSSZ><suffix>` and so is every later part. If the run ends with one part, it is renamed to the plain `--output`, so existing scripts keep working. Later parts stream to a `.partial` named after their own first ping, so no rename needs lookahead. Two parts whose first ping falls in the same UTC second get `_2`, `_3`... appended before the suffix (checked against names used this run). Time uses the ping's stamp (not wall clock), UTC.
5. **Failure.** Finished parts stay. On any `BaseException` only the current `.partial` is deleted. If part 1 had been waiting for the plain name it is finalised with its time name at that moment (a failed run is never a "single part" run). `main` prints the finished-part list and returns non-zero for a handled error; for an exception it prints the list then re-raises. Zero pings overall still returns 1 with no files.
6. **Stale parts.** After a successful run, delete files in the output directory that match this run's part pattern `<stem>_\d{8}T\d{6}Z(_\d+)?<suffix>` and were not written by this run, and print their names. A multi-part run does not touch a plain `--output` file from an earlier run, but warns that it exists. Nothing else in the directory is touched. On failure nothing stale is removed.
7. **Options** (all in `_build_parser`):
   - `--max-part-size-mb` (float, default 500; 0 means no size limit). MB = 10^6 bytes, so a part never exceeds that.
   - `--max-part-duration-min` (float, default off).
   - `--gap-split-s` (float, default 60; 0 means off). Separate from `_MAX_SECONDS_PER_PING`, which is the ping-interval field's rule.
   - `--no-split` turns all three off and gives today's single file.
   Report: print each part with its ping count and size, plus the reason each rollover happened (size, duration, gap).
8. **Docs.** README section for `bag_to_xtf` and `--help` text: options, naming rule, single-part keeps plain name, failure behaviour, stale-part cleanup.
9. **Check on real data.** Convert one 2+ GB Appledore bag with the default, with `--no-split` for comparison, and with a small size and a small gap, output only under `/home/roland/project11/.agent/scratchpad/xtf_split/`. Compare: sum of part pings equals the unsplit count, and the part contents concatenated (pings only, without file headers) match the unsplit file's pings byte for byte. Look at real gaps in the bags to confirm or move the 60 s default. Bags are read only.

## Tests

New `test_xtf_split.py` using a fake `iter_messages` (monkeypatch) and small synthetic pings, plus writer tests:
- Writer: `ping_size` equals the bytes `write_ping` actually adds, in both layouts; `bytes_written` equals file length.
- Size split: no part exceeds the limit (assert on file sizes), oversized single ping gets its own part, concatenated ping bytes equal the unsplit file's, `ping_number` runs continuously, first ping of part 2 carries speed and seconds-per-ping from the last ping of part 1. Both layouts.
- Duration split alone and combined with size (first reached wins).
- Gap split: gap longer than threshold starts a new part; shorter does not; `--gap-split-s 0` and `--no-split` give one file.
- Naming: one part gets the plain name; more than one gets time names and no plain file; same-second collision gives `_2`.
- `--max-pings` across parts; `--start-time`/`--end-time` trimming with splitting.
- Failure: exception mid-run in part 2 leaves finished parts, no `.partial`, part 1 has its time name; a failure in part 1 leaves nothing.
- Zero pings: exit 1, no files.
- Stale parts: an earlier run's parts are removed on success, unrelated files and a plain-name file remain (plain one warned about), nothing removed on failure.

## Files to Change

| File | Change |
|------|--------|
| `bag_analysis/bag_analysis/cli/bag_to_xtf.py` | `_iter_pings` generator, `_PartSink`, split options, naming, failure and stale-part handling, report |
| `bag_analysis/bag_analysis/xtf/writer.py` | `ping_size`, `bytes_written` |
| `bag_analysis/test/test_xtf_writer.py` | size tests |
| `bag_analysis/test/test_xtf_split.py` (new) | split tests above |
| `bag_analysis/README.md` | `bag_to_xtf` section |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Robustness / error handling | Failure and stale-file rules are specified and tested; no silent partial sets |
| Tests with the change | Both layouts, `--max-pings`, trimming, cleanup covered |
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

- **Stale docs** (must land in this PR): `bag_analysis/README.md` `bag_to_xtf` section and the module docstring (it currently says one `.partial` and one rename).
- **Agent-instruction candidates**: None. This repo has no `.agents/README.md`, a separate task.

## Open Questions

- Stale-part deletion on success (step 6) is my choice; the alternative is to refuse to run when stale parts exist. Is deleting generated parts acceptable?
- Option names and the 10^6-byte MB are my choices.
- Is 60 s a good gap default? Confirm against real bags in step 9.

## Estimated Scope

Single PR (PR 1). PR 2 is a separate plan: measured turn thresholds on real bags (including 0.5 m/s tracks), a nav-only first pass, drop-turn option.
