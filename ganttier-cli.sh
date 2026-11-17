#!/bin/sh
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
if [ -n "$GANTTIER_PYTHON" ]; then
    PYTHON=$GANTTIER_PYTHON
elif [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON=$ROOT/.venv/bin/python
elif [ -x "$HOME/gv/bin/python" ]; then
    PYTHON=$HOME/gv/bin/python
else
    PYTHON=python3
fi
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
cd "$ROOT/backend" || exit 1
exec "$PYTHON" -m ganttier.cli "$@"
