# Camoufox remote collection kit

Recovered from OpenCode chat **Crawl 100 CanLII decisions with Camoufox**,
session `ses_e30f957f8ffeRwWsiqdqpeJEpk`. Original files remain in
`/home/headrun/Desktop/Thanush`. The last chat reply's SSH snippet is invalid;
use this kit instead. Local original collection: 124 unique URLs and hashes,
all matching files, independently checked on 12 November 2026.

## Deployment used

- SSH: `interns@37.27.51.7`, existing SSH key authentication.
- Isolated directory: `/home/interns/camoufox-remote-20261112-50`.
- Existing interpreter: `/opt/cloak/venv/bin/python` (Camoufox browser
  `158.0.1-beta.41`, Python 3.10.12, camoufox 0.5.9, Playwright 1.62.0);
  no sudo or system changes needed. Package versions are pinned in requirements.txt.
- Two headless workers; target 50 unique fresh decisions in `remote-data`.
- Credentials are read from a mode-600 `.env.dataimpulse` file, excluded from
  this kit and archive. Do not use SSH usernames/passwords as proxy credentials.
- Other crawler releases, previews, and running jobs are left in place.

## Run

On an already configured host, select its Camoufox interpreter. On a fresh host,
run `./remote_setup.sh` to install a user-owned virtual environment; administrator
help may be needed for missing Firefox libraries.

```bash
cd /home/interns/camoufox-remote-20261112-50
set -a
. ./.env.dataimpulse
set +a
NWORKERS=2 GOAL=50 OUT=remote-data PYTHON=/opt/cloak/venv/bin/python \
  nohup ./run_parallel.sh > launch.log 2>&1 < /dev/null &
```

Resume by running the same command with the same output directory. Use a new
output directory for a fresh target. A directory lock prevents duplicate launches.
The launcher makes one bounded attempt per worker and does not automatically
relaunch exhausted or blocked workers. Inspect evidence before any retry.

```bash
tail -f remote-data/worker-1.log remote-data/worker-2.log
/opt/cloak/venv/bin/python summarize_crawl.py --out remote-data --goal 50
cat remote-data/result.json
```

Only `result.json` with `goal_reached: true` establishes completion. The verifier
requires 50 unique URLs and hashes, valid judgment containers, and matching
SHA-256 object files. It writes `decisions.csv` and exits nonzero if incomplete.
Blocked/error pages are evidence and are never counted toward the target.
Existing local records are not copied into the remote output directory.
The included `seed_urls.txt` contains 200 decision URLs discovered by the original
local run. The launcher seeds a new directory from these URLs. This was necessary
because remote `ajaxSearch.do` discovery returned HTTP 406, while direct decision
fetches succeeded. If you remove the seed list, in-browser discovery is attempted.

## Corrections from the recovered kit

- Unpack the `(success, reason)` wall result; reject hard restrictions and soft
  error pages, and stop on persistent collection walls or HTTP 429.
- Apply worker slices after discovery, including its owner and waiting workers.
- Lock store operations and recheck the shared count to enforce exactly 50.
- Use UTC timestamps, bounded proxy screening and probe attempts, kernel-backed
  discovery locks, a total cap of 12 browser sessions per worker, 60-second
  fetch timeouts, and no automatic supervisor restart loop.
- Use the selected virtual environment for every worker and final verification.
- Verify hashes and judgment structure rather than counting capture-log lines.

## Offline checks

```bash
python3 -m py_compile *.py
bash -n run_parallel.sh remote_setup.sh
python3 -m unittest discover -s . -p test_crawler.py -v
```

This validates local behavior. Live access is determined separately by the run;
previous successful captures do not guarantee future CanLII access.

## Verified result — 12 November 2026

Remote collection completed: **50 unique decision URLs, 50 distinct hashes,
0 invalid records**. Seven blocked-probe records were preserved and excluded.
Every saved decision passed the judgment-container and SHA-256 checks both on
the server and after downloading. The finalized launcher also passed a resume
run at 50/50. Four offline regression tests passed locally and on the remote host.
