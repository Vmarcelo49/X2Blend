"""Imports every .X and character file under a data directory, inside Blender, and reports what it could not do.

    blender --background --factory-startup --python tests/smoke_corpus.py -- <data dir>

This shows the importer runs across the corpus and lists its warnings. It says nothing about
pose accuracy, which tests/test_import.py measures per model.
"""

import glob
import os
import sys
import time
import traceback
from collections import Counter

import bpy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from x2blend.build import import_file  # noqa: E402

data = sys.argv[sys.argv.index("--") + 1]
paths = sorted(glob.glob(os.path.join(data, "*", "*.X")) + glob.glob(os.path.join(data, "*", "*.x")))
# item/NNN.dat and the other data files are not character files.
paths += sorted(glob.glob(os.path.join(data, "*", "[0-9][0-9].dat")))
paths = [p for p in paths if os.path.basename(os.path.dirname(p)) != "item" or not p.endswith(".dat")]
failed = warned = 0
kinds = Counter()
started = time.time()
for path in paths:
    print(f"SMOKE {os.path.relpath(path, data)}", flush=True)  # names the file if Blender itself crashes
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        _, warnings = import_file(path)
    except Exception:
        failed += 1
        print(f"SMOKE FAIL {os.path.relpath(path, data)}\n{traceback.format_exc()}")
        continue
    if warnings:
        warned += 1
        for warning in warnings:
            kinds[warning.split("'")[0].split('"')[0]] += 1
        print(f"SMOKE WARN {os.path.relpath(path, data)}: {len(warnings)}, e.g. {warnings[0]}")
print(f"SMOKE {len(paths)} files, {failed} failed, {warned} with warnings, {time.time() - started:.0f}s")
for kind, count in kinds.most_common():
    print(f"SMOKE {count:6d} x {kind}")
raise SystemExit(1 if failed else 0)
