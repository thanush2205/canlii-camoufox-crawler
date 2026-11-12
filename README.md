# CanLII Camoufox crawler

A complete Python and Bash kit for collecting decision pages on a remote Linux
server: two browser workers, resumable content-addressed storage, a shared exact
goal, and independent verification of the saved documents.

## Verified result

On **12 November 2026**, the remote run collected **50 fresh decisions**, with
50 unique URLs, 50 distinct SHA-256 hashes, and zero invalid records. Seven
blocked-probe records were saved as evidence and excluded from the total.
The downloaded collection was independently verified, and resume at 50/50 passed.
Four offline regression tests passed locally and on the remote server.

Search discovery returned HTTP 406 remotely. The successful run used the included
200 previously discovered decision URLs as seeds and freshly fetched every saved
page on the server. Previous success does not guarantee future site access.

## Setup and run

Requires Linux, Bash, Python 3.10+, and `flock` (normally supplied by util-linux).
Use a configured Camoufox environment or install one as your regular SSH user:

```bash
bash remote_setup.sh
cp .env.example .env
chmod 600 .env
# Edit .env with your own proxy credentials.
set -a
. ./.env
set +a
NWORKERS=2 GOAL=50 OUT=canlii_data PYTHON=.venv/bin/python \
  bash run_parallel.sh
```

If Firefox system libraries are missing, setup prints the administrator command.
The launcher seeds new output directories from `seed_urls.txt`. Reuse the same
output directory to resume; use a different directory for a fresh collection.

```bash
.venv/bin/python summarize_crawl.py --out canlii_data --goal 50
cat canlii_data/result.json
```

Completion requires `goal_reached: true`, 50 distinct URLs and hashes, valid
judgment containers, and no corrupt objects. Blocked pages do not count.

## Kit contents

| File | Purpose |
| --- | --- |
| `canlii_crawler.py` | Probe, discover, fetch, store, and resume |
| `run_parallel.sh` | Worker launch, directory lock, and final verification |
| `remote_setup.sh` | User-owned Camoufox environment installation |
| `summarize_crawl.py` | Validate objects and build the CSV/result report |
| `seed_urls.txt` | 200 decision URL seeds |
| `requirements.txt` | Versions used by the verified remote environment |
| `test_crawler.py` | Offline regression tests |
| `README_REMOTE.md` | Operational details and deployment notes |
| `investigation.json` | Recovery provenance and verification record |
| `evidence/` | Verified 50-decision collection, CSV, and result |

The evidence archive includes captured HTML, provenance, and worker logs. It
contains no environment file or SSH keys. Runtime output and credentials are
ignored by Git. Use this kit only where you are authorized to retrieve content.

## Offline checks

```bash
python3 -m py_compile *.py
bash -n run_parallel.sh
bash -n remote_setup.sh
python3 -m unittest discover -s . -p test_crawler.py -v
```

The tests require no proxy credentials, browser installation, or network access.
They cover wall handling, concurrent exact-goal storage, corrupt-file detection,
and the total session cap.
