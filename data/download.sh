#!/bin/sh
# Fetch the two real HTTP traces used as workloads (Internet Traffic Archive).
cd "$(dirname "$0")" || exit 1
curl -sS -O https://ita.ee.lbl.gov/traces/NASA_access_log_Jul95.gz
curl -sS -O https://ita.ee.lbl.gov/traces/clarknet_access_log_Aug28.gz
