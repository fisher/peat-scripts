#!/bin/bash

if [ -r ./common ]; then
    . common
elif [ -r ~/peat-scripts/testrun/common ]; then
    . ~/peat-scripts/testrun/common
fi

set -euo pipefail

NODE_URL="http://${EXTIP}:${TCPP}"

call() {
  local url="${1}/peat.sidecar.v1.PeatSidecar/${2}"
  local body
  local code
  body=$(curl --silent --show-error \
    -w '\n%{http_code}' \
    -X POST "$url" \
    -H 'Content-Type: application/json' \
    -d "${3}")
  code=$(printf '%s' "$body" | tail -n1)
  body=$(printf '%s' "$body" | sed '$d')
  if [ "$code" != "200" ]; then
    echo "ERROR: $url returned HTTP $code" >&2
    echo "  body: $body" >&2
    return 1
  fi
  printf '%s' "$body"
}


COLLECTION=telemetry
DOC_ID="stat-${HOSTNAME}"

while [ true ]; do
  NR_FP=`head -1 /proc/vmstat |awk '{print $2}'`
  TSH=`date '+%g-%m-%d %T %N'`
  PAYLOAD="{\"free_pages\":\"${NR_FP}\",\"time_hr\":\"${TSH}\"}"

  echo "PutDocument ${COLLECTION}/${DOC_ID}"
  call "${NODE_URL}" PutDocument \
  "{\"collection\":\"${COLLECTION}\",\"docId\":\"${DOC_ID}\",\"jsonData\":$(jq -Rs <<<"$PAYLOAD")}" \
  >/dev/null
  echo "  wrote: ${PAYLOAD}"
  sleep 1
done

