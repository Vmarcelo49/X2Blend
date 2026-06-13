#!/usr/bin/env bash
# x2blend.sh — Full .x → .blend pipeline
#
# Usage: ./x2blend.sh <input.x> <output.blend>
#
# Prerequisites:
#   - wine (to run x2blend.exe)
#   - python3 with bpy installed  (pip install bpy)
#   - build/x2blend.exe already compiled (run build.sh first)
#
# The script:
#   1. Converts the .x file to an intermediate JSON via x2blend.exe (Wine)
#   2. Passes the JSON to scripts/blend_importer.py (native bpy) to save .blend
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXE="${SCRIPT_DIR}/build/x2blend.exe"
IMPORTER="${SCRIPT_DIR}/scripts/blend_importer.py"
if [[ $# -lt 2 ]]; then
echo "Usage: $0 <input.x> <output.blend>"
exit 1
fi
INPUT_X="$(realpath "$1")"
OUTPUT_BLEND="$(realpath "$2")"
TMP_JSON="$(mktemp /tmp/x2blend_XXXXXX.json)"
cleanup() { rm -f "${TMP_JSON}"; }
trap cleanup EXIT
echo "[x2blend] Step 1/2: Loading .x file with Wine..."
wine "${EXE}" "${INPUT_X}" "${TMP_JSON}"
echo "[x2blend] Step 2/2: Building .blend with Python bpy..."
PYTHON="${SCRIPT_DIR}/.venv/bin/python"
if [[ -x "${PYTHON}" ]] && "${PYTHON}" -c "import bpy" &>/dev/null; then
"${PYTHON}" "${IMPORTER}" "${TMP_JSON}" "${OUTPUT_BLEND}"
elif python3 -c "import bpy" &>/dev/null; then
python3 "${IMPORTER}" "${TMP_JSON}" "${OUTPUT_BLEND}"
elif command -v blender &>/dev/null; then
echo "[x2blend] 'bpy' Python package not found. Using system Blender executable..."
env -u PYTHONHOME -u PYTHONPATH -u VIRTUAL_ENV PATH="/usr/bin:${PATH}" blender --background --python "${IMPORTER}" -- "${TMP_JSON}" "${OUTPUT_BLEND}"
else
echo "[x2blend] ERROR: Neither 'bpy' Python package nor 'blender' executable was found."
echo "          Please install 'bpy' (pip install bpy) or install Blender."
exit 1
fi
echo "[x2blend] Done! Output: ${OUTPUT_BLEND}"
