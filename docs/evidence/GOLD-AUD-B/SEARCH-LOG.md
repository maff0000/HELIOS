# Search method and negative-search record (CA item 2)

A negative is a claim. This file records exactly what was searched so the claim can be re-run and extended. Conclusion, bounded: **no completed HERMES Redis to new-HELIOS integration was found in the locations below; absence elsewhere is UNVERIFIED.**

## Locations searched, with the command class used

| # | Location | Method (read-only) | Result |
|---|---|---|---|
| L1 | maff0000/HELIOS main, all branches | fresh clone; `git branch -r`; `git log --all --oneline`; `grep -rni redis` over source, docs, config, tests | three branches (main, proof branch, vertical-slice branch). The word appears only in three guard tests that FORBID importing a Redis client (`tests/test_contract_cer.py:259`, `tests/test_execution_blind.py:260`, `tests/test_contract_hermes.py:116`). No client, no sink. |
| L2 | a private strategy runtime repository, main | fresh clone; `git grep -il redis`; read its README and design records | no Redis reader found; its records are consistent with L1 |
| L3 | maff0000/HSA main | fresh clone; `git grep` for the HELIOS package schema id and `evaluate_on` | none: HSA emits no HELIOS-format packages |
| L4 | maff0000/hermes at the audited SHA | fresh clone; `git grep -il helios`; reading publisher modules and work orders | HERMES-side WOs address HELIOS as a future consumer and explicitly exclude writing the HELIOS-side reader (see INVENTORY C10) |
| L5 | maff0000/falcon main | fresh clone; `git grep` for candle/indicator key names and HELIOS | no reader; one HELIOS discovery record (INVENTORY C16) |
| L6 | GitHub code search, owner-wide, default branches | `gh api -X GET search/code` for five phrases (`hermes:candles`, `hermes:indicators`, `hermes.candles.latest`, `MarketFactFrame`, `helios.market_fact`); counts per repository kept in the private appendix | HERMES key names appear in: hermes, cerberus, a private ARES repository, a private FALCON repository, GOAL. `MarketFactFrame` only in HELIOS and GOAL. Search stopped at the API rate limit; results per repo for the `hermes:indicators` phrase in cerberus and a private ARES repository were not obtained (UNVERIFIED) |
| L7 | Owner repository list | `gh repo list` (list kept privately) | repositories not opened: all except those in the table at README. Of these, names suggesting relevance (a standalone legacy HELIOS repository, a legacy GOLD repository, a legacy HERMES repository) were: legacy HELIOS repository OPENED (INVENTORY C6); the others NOT opened (UNVERIFIED) |
| L8 | Local worktrees and checkouts on the dev host and the trader host (names only) | `find` by name for helios, bridge, watcher, compositor; read source of those found | C6 to C9 in INVENTORY |
| L9 | systemd unit files on the dev host | read the unit files by path (`ls`, `grep -E '^(Description|ExecStart|WorkingDirectory)'`); `systemctl` was NOT used | the two legacy HELIOS units are not active; no unit starts a HERMES-to-HELIOS reader |
| L10 | Process list on the dev host | `ps -eo pid,etime,cmd` filtered | no process matched a HERMES-to-HELIOS reader or the two untracked Redis probe scripts; other findings reported privately (G-12) |
| L11 | A dated local legacy archive | checksum verification (all OK); extracted into a private scratch directory; `grep -rIn` for Redis clients and HERMES key names; cloned the bundle | C6 |
| L12 | Untracked probe scripts and captured sample on the dev host | read source only | C5, C11 |
| L13 | Legacy state-store key names | **NOT searched.** The dispatch brief forbids connecting to Redis; Recorded UNVERIFIED | UNVERIFIED |
| L14 | Container names and labels | **NOT queried** (`docker ps` not run; the brief limits the audit to source and GitHub inspection). Local image tags listed once with `docker images` | UNVERIFIED for running containers |
| L15 | FALCON PID-07/08 bodies; GOLD-AUD-A output | not read; A output not available to this audit | UNVERIFIED |

## Commands (sanitised; targets abbreviated)

```
git clone https://github.com/maff0000/<repo>.git   # HELIOS, a private runtime repository, HSA, hermes, falcon, cerberus, a private ARES repository
git checkout -q -b wo/GOLD-AUD-B-evidence origin/main            # HELIOS
git checkout -q ca1901789f8c7a7e085d02ebde8f7264d90d669e         # hermes
gh repo list maff0000 --limit 300 --json name,isPrivate,isArchived,pushedAt
gh api -X GET search/code -f q='"<phrase>" user:maff0000'        # five phrases
git -C HELIOS grep -rni redis -- .                               # L1
git -C hermes grep -il helios ; git -C hermes grep -n "<key phrases>"
find <dev-host trees> -maxdepth 3 -iname '*helios*'              # L8
sha256sum -c SHA256SUMS.txt ; tar xzf <archive parts> -C <scratch>   # L11 (archive is the dev-host backup)
grep -rIn -i "import redis\|hermes:candles\|hermes:indicators" <scratch>
ps -eo pid,etime,cmd | grep -i '<names>'                         # L10
```

No write, no `docker exec`, no unit-state change, no legacy-state-store or Redis access, no push. See [SAFETY.md](SAFETY.md).

## What the negative does and does not establish

- Established (PROVEN): within L1 to L5, L8, L11, no source file contains a HERMES-Redis-to-HELIOS-fact reader. The newer HELIOS cannot contain one (guard tests) and its proof branch records that none was built.
- Not established: any reader in repositories not opened (L7), any running or scheduled reader not visible in L9/L10, anything in the legacy state store (L13) or in container state (L14), and any change after the audited SHAs.
- Contradiction check: no Lane A output was available; if Lane A later reports a HELIOS-side consumer of the live publication, this negative must be re-examined (PID stop condition).
