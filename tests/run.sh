#!/bin/sh
# Runs every check. DAYBREAK_DECOMP points at a daybreak-decomp checkout with the game
# install and fetched dependencies; the tests never copy game data into this repository.
# Without it the game-backed checks are reported as NOT RUN and the exit status is 77.
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
decomp=${DAYBREAK_DECOMP:-$root/../daybreak-decomp}
blender=${BLENDER:-blender}

python3 -I "$root/tests/test_xfile.py"

data=$decomp/Higurashi-Daybreak/data
if [ ! -f "$data/keiichi/00.X" ] || [ ! -f "$decomp/src/assets/xmodel.h" ]; then
    echo "NOT RUN: game-backed checks (no install or xmodel.h under $decomp)"
    exit 77
fi

mkdir -p "$root/build"
oracle=$root/build/x_oracle
if [ ! -x "$oracle" ] || [ "$root/tests/oracle/x_oracle.cpp" -nt "$oracle" ] ||
   [ "$decomp/src/assets/xmodel.h" -nt "$oracle" ]; then
    g++ -std=c++17 -O1 -I"$decomp/src/assets" -I"$decomp/third_party/glm" \
        "$root/tests/oracle/x_oracle.cpp" -o "$oracle"
fi

run() { # file under data/, then extra test arguments
    file=$1
    shift
    json=$root/build/$(echo "$file" | tr '/' '_').json
    "$oracle" "$data/$file" "$json"
    echo "== $file"
    log=$root/build/test.log
    status=0
    "$blender" --background --factory-startup --python-exit-code 1 \
        --python "$root/tests/test_import.py" -- "$data/$file" "$json" "$@" > "$log" 2>&1 || status=$?
    grep -E '^(FAIL|OK|reader|import|mesh|keys|poses|deformation|[0-9]+ failure|Traceback|  File|[A-Za-z]*Error)' "$log" || true
    rm -f "$json"
    [ "$status" -eq 0 ] || { echo "FAILED: $file (full log: $log)"; exit 1; }
}

# The other two encodings, reader only: bzip and text.
run aura/00.X --reader-only
run item/03.X --reader-only
run keiichi/00.X

weapon() { # character file, the weapon id ASSET-CATALOG 3.3 pins for it, the item model 0 file
    rig=$root/build/rig.json
    item=$root/build/item.json
    "$oracle" "$data/$(dirname "$1")/00.X" "$rig"
    "$oracle" "$data/item/$3" "$item"
    echo "== $1 (weapon $2, $3)"
    log=$root/build/test.log
    status=0
    "$blender" --background --factory-startup --python-exit-code 1 \
        --python "$root/tests/test_weapon.py" -- "$data/$1" "$rig" "$item" "$2" > "$log" 2>&1 || status=$?
    grep -E '^(FAIL|OK|weapon|warnings|Traceback|  File|[A-Za-z]*Error)' "$log" || true
    rm -f "$rig" "$item"
    [ "$status" -eq 0 ] || { echo "FAILED: $1 (full log: $log)"; exit 1; }
}

weapon keiichi/00.dat 5 02.X
weapon keiichi/01.dat 18 40.X
weapon keiichi/02.dat 34 103.X
