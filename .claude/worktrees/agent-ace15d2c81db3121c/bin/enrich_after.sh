#!/bin/bash
# Wait for the in-flight backfill (PID 36064) to finish, then run the enriched
# sweep: creates any remaining days with full metadata AND patches every
# existing event to add tags/project/dur. Idempotent + resumable.
until ! kill -0 36064 2>/dev/null; do sleep 60; done
echo "=== prior sweep finished $(date); starting enrichment ===" >> ~/.cache/0r/backfill-enrich.log
python3 ~/bin/backfill_jbm_archive.py 2026-06-12 >> ~/.cache/0r/backfill-enrich.log 2>&1
echo "=== enrichment finished $(date) ===" >> ~/.cache/0r/backfill-enrich.log
touch ~/.cache/0r/ENRICH_COMPLETE
