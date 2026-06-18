#!/usr/bin/env bash
# x2blend.sh — Full .x -> .blend pipeline orchestrator.
#
# Usage:
#   ./x2blend.sh <input.x> <output.blend> [stage1-flags...] [-- stage2-flags...]
#
# Prerequisites:
#   - wine (to run x2blend.exe under)
#   - python3 with bpy installed (pip install bpy), OR a system blender
#   - build/x2blend.exe and build/viewer.exe already compiled (run build.sh)
#
# Stage 1 (x2blend.exe, run under Wine) consumes the .x file and emits an
# intermediate JSON.  Stage 2 (blend_importer, run via bpy or system
# Blender) consumes the JSON and writes the .blend.
#
# All arguments after the input/output paths up to `--` are forwarded
# verbatim to x2blend.exe (Stage 1).  All arguments after `--` are
# forwarded verbatim to the Python importer (Stage 2).  If no `--` is
# present, every remaining flag goes to Stage 1.
#
# Stage 1 flags (x2blend.exe):
#   --no-bake                       Use TRS-remap instead of baking (experimental).
#   --bake-fps N                    Bake sample rate (default 60).
#   --max-influences N              Bone-influence cap (default 4).
#   --log-level <debug|info|warn|error>
#
# Stage 2 flags (blend_importer):
#   --root-scale N                  Scale applied to root objects (default 1.0).
#                                   Pass --root-scale 0.01 for Higurashi
#                                   Daybreak assets (the original hardcoded
#                                   this value; the refactor makes it opt-in).
#   --bone-tail-length N            Visual bone tail length (default 0.05).
#   --max-influences N              Info only (capping happens C++-side).
#   --decimate N                    Decimation ratio or error tolerance.
#   --decimate-mode ratio|error     Default 'ratio'; 'error' is headless-safe.
#                                   Recommended for headless: --decimate-mode
#                                   error --decimate 1e-4.
#   --no-decimate                   Disable decimation (overrides --decimate).
#   --log-level DEBUG|INFO|WARN|ERROR
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXE="${SCRIPT_DIR}/build/x2blend.exe"
IMPORTER_PKG="blend_importer"
IMPORTER_DIR="${SCRIPT_DIR}/scripts"

usage() {
    echo "Usage: $0 <input.x> <output.blend> [stage1-flags...] [-- stage2-flags...]"
    echo ""
    echo "Stage 1 flags go to x2blend.exe (Wine):"
    echo "  --no-bake  --bake-fps N  --max-influences N  --log-level <debug|info|warn|error>"
    echo "Stage 2 flags go to the Python importer (after '--'):"
    echo "  --root-scale N  --bone-tail-length N  --max-influences N"
    echo "  --decimate N  --decimate-mode ratio|error  --no-decimate"
    echo "  --log-level DEBUG|INFO|WARN|ERROR"
    echo ""
    echo "For Higurashi Daybreak assets, pass:  -- --root-scale 0.01"
    echo "For headless F-curve decimation, pass:  -- --decimate-mode error --decimate 1e-4"
}

if [[ $# -lt 2 ]]; then
    usage
    exit 1
fi

INPUT_X="$(realpath "$1")"
OUTPUT_BLEND="$(realpath "$2")"
shift 2

# Split remaining args at the first `--`:  before -> stage 1, after -> stage 2.
STAGE1_ARGS=()
STAGE2_ARGS=()
SEEN_SEPARATOR=0
for arg in "$@"; do
    if [[ "${SEEN_SEPARATOR}" -eq 0 && "${arg}" == "--" ]]; then
        SEEN_SEPARATOR=1
        continue
    fi
    if [[ "${SEEN_SEPARATOR}" -eq 1 ]]; then
        STAGE2_ARGS+=("${arg}")
    else
        STAGE1_ARGS+=("${arg}")
    fi
done

TMP_JSON="$(mktemp /tmp/x2blend_XXXXXX.json)"
TMP_LAUNCHER="$(mktemp /tmp/x2blend_launcher_XXXXXX.py)"
cleanup() {
    rm -f "${TMP_JSON}" "${TMP_LAUNCHER}"
}
trap cleanup EXIT

# Write a tiny launcher so `blender --background --python <launcher> -- args`
# works against the package.  The launcher adds scripts/ to sys.path, then
# delegates to blend_importer.main:main().  Using a file (rather than
# --python-expr) avoids quoting issues with stage-2 args that contain
# spaces or special characters.
cat >"${TMP_LAUNCHER}" <<EOF
import sys
sys.path.insert(0, ${IMPORTER_DIR@Q})
from ${IMPORTER_PKG}.main import main
main()
EOF

echo "[x2blend] Step 1/2: Loading .x file with Wine (x2blend.exe)..."
if [[ ${#STAGE1_ARGS[@]} -gt 0 ]]; then
    echo "[x2blend]   stage1 args: ${STAGE1_ARGS[*]}"
fi
wine "${EXE}" "${INPUT_X}" "${TMP_JSON}" "${STAGE1_ARGS[@]}"

echo "[x2blend] Step 2/2: Building .blend (blend_importer)..."
if [[ ${#STAGE2_ARGS[@]} -gt 0 ]]; then
    echo "[x2blend]   stage2 args: ${STAGE2_ARGS[*]}"
fi

VENV_PY="${SCRIPT_DIR}/.venv/bin/python"

run_via_module() {
    # run_via_module <python-binary>
    local py="$1"
    PYTHONPATH="${IMPORTER_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
        "${py}" -m "${IMPORTER_PKG}" "${TMP_JSON}" "${OUTPUT_BLEND}" "${STAGE2_ARGS[@]}"
}

if [[ -x "${VENV_PY}" ]] && "${VENV_PY}" -c "import bpy" &>/dev/null; then
    echo "[x2blend]   using venv bpy: ${VENV_PY}"
    run_via_module "${VENV_PY}"
elif python3 -c "import bpy" &>/dev/null; then
    echo "[x2blend]   using system python3 with bpy"
    run_via_module python3
elif command -v blender &>/dev/null; then
    echo "[x2blend]   'bpy' python package not found; using system blender"
    # blender --background --python launcher -- args...  causes sys.argv
    # inside the launcher to be ['launcher.py', 'args...'].  blend_importer's
    # main() strips the leading script-name element via _strip_blender_separator.
    env -u PYTHONHOME -u PYTHONPATH -u VIRTUAL_ENV PATH="/usr/bin:${PATH}" \
        blender --background \
        --python "${TMP_LAUNCHER}" -- "${TMP_JSON}" "${OUTPUT_BLEND}" "${STAGE2_ARGS[@]}"
else
    echo "[x2blend] ERROR: Neither 'bpy' python package nor 'blender' executable was found."
    echo "          Please install 'bpy' (pip install bpy) or install Blender."
    exit 1
fi

echo "[x2blend] Done! Output: ${OUTPUT_BLEND}"
