#!/bin/sh
# Fetch the NASA July 1995 HTTP trace (1,891,714 requests) used as the real workload.
cd "$(dirname "$0")" && curl -sS -O https://ita.ee.lbl.gov/traces/NASA_access_log_Jul95.gz
