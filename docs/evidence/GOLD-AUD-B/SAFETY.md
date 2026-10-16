# Safety and deployment constraints (CA item 9)

## Attestation

- Read-only audit. No broker call, no deployment, no service start/stop/restart, no unit-state change, no configuration or credential change, no production or DEV data mutation, no backup, no contract freeze, no push, no pull request, no GitHub comment.
- No connection to any Redis, database, Graylog, MT5 or executor service. The legacy state store was not searched. No `docker exec`. `systemctl` not used. `docker ps` not run.
- Container use, exactly: three throwaway containers started with networking disabled, a read-only root and all capabilities dropped, run to completion and removed, to (a) reproduce the probe, (b) load the two HSA packages through HELIOS's own loader. Inputs were mounted read-only. One `docker images` listing of local image names.
- HELIOS Docker-runtime tests (which would build and run an image and use `docker exec`) were not run; see TESTS-AND-REPRODUCTION 1.
- Inputs of the deployed instances were read in place for a privately reported finding (G-12), with no write. The process list was read once. Archive extraction and legacy clones went to a private scratch directory; clones of other repositories were made into the audit workspace only.
- No second reader: nothing in this audit connects to HERMES Redis or stands up an adapter. Two separate existing readers (CERBERUS, ARES) were read as source only.
- GitHub API: GET only (repository list, code search). The code search stopped at the API rate limit.

## Public-safety attestation

This directory contains no sensitive values, credentials, broker or account identifiers, internal hostnames, IP addresses, ports, credential file locations, exposure mechanics or live configuration values. Private-repository and private-archive content is cited only generically, without names, revisions, paths or checksums. Detail that is not public-safe is held in a private location by the maintainers.

Checks run on the final tree: the repository scan tool named in the PID acceptance list (not present in HELIOS, G-19) was run from the GOAL repository against the HELIOS tree: exit status 0, `RESULT: CLEAN` (working-tree scan CLEAN, git-history scan CLEAN). A grep over this directory for IPv4-like strings, credential-word patterns and private repository or host names returned no hit. The independent auditor should repeat both.

## Stop conditions

None triggered. No candidate reader was found running against live HERMES Redis (L10). One finding about the deployed stack was reported privately (G-12); untouched.
