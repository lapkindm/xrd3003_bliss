#!/bin/bash
# Run tests/bliss_e2e.py against simulated hardware: a throwaway Beacon (port
# 25001, Redis 6391/6392), the fake generator from isovolt_driver and the fake
# PNOZ door relay. Nothing touches the instrument or its Beacon (port 25000).
#
#   tests/run_e2e.sh            (from the xrd3003_bliss folder)
#
# BLISS_ENV: the conda environment with Bliss (default bliss_env);
# ISOVOLT: the isovolt_driver checkout (default: next to this repository).

HERE=$(cd "$(dirname "$0")/.." && pwd)
E=${BLISS_ENV:-$HOME/miniconda3/envs/bliss_env}
ISOVOLT=${ISOVOLT:-$HERE/../isovolt_driver}
export CONDA_PREFIX=$E PATH=$E/bin:$PATH

if ss -ltn | grep -q ":25001\b"; then
    echo "port 25001 is in use (another test Beacon?): stop it first"; exit 2
fi
R=$(mktemp -d /tmp/xrd_e2e.XXXXXX)
mkdir -p "$R/cfg/sessions" "$R/logs" "$R/data"
LINK=$R/gen
PLINK=$R/pnoz
sed -e "s#<LINK>#$LINK#" -e "s#<PLINK>#$PLINK#" "$HERE/tests/e2e_config.yml" > "$R/cfg/test.yml"
printf "plugin: session\n" > "$R/cfg/sessions/__init__.yml"
printf "class: Session\nname: xrd_test\nconfig-objects: [mythen, shutter, a2, mx, doors, generator]\n" \
    > "$R/cfg/sessions/xrd_test.yml"

python "$HERE/tests/fake_pnoz.py" "$PLINK" > "$R/pnoz.out" 2>&1 & PNOZ=$!
python "$ISOVOLT/tests/fake_generator.py" "$LINK" > "$R/gen.out" 2>&1 & GEN=$!
# short socket paths: Unix sockets are limited to ~100 characters
(cd "$R" && exec setsid beacon-server --db-path "$R/cfg" --port 25001 \
    --redis-port 6391 --redis-data-port 6392 \
    --redis-socket "$R/r.sock" --redis-data-socket "$R/rd.sock" \
    --homepage-port 9011 --log-server-port 9021 --log-output-folder "$R/logs" \
    --log-viewer-port 0 > "$R/beacon.log" 2>&1) & BCN=$!
for _ in $(seq 30); do ss -ltn | grep -q ":25001\b" && [ -e "$LINK" ] && break; sleep 1; done
sleep 2

cd "$HERE"
XRD_TEST_DATA=$R/data BEACON_HOST=localhost:25001 PYTHONPATH=. PYTHONFAULTHANDLER=1 \
    timeout -s ABRT 900 python -u -W ignore tests/bliss_e2e.py "$LINK" "$PLINK"
RC=$?

kill -TERM -- -"$(ps -o pgid= $BCN | tr -d ' ')" 2>/dev/null
kill $GEN $PNOZ 2>/dev/null
sleep 2; wait 2>/dev/null
echo "== exit code $RC (logs in $R)"
exit $RC
