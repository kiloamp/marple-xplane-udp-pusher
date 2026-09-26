#!/bin/zsh
cd -- "${0:A:h}" || exit 1
python_bin="$PWD/.venv/bin/python3"
if [[ ! -x "$python_bin" ]]; then
  python_bin=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
fi
if [[ ! -x "$python_bin" ]]; then
  python_bin=$(command -v python3)
fi
"$python_bin" start_xplane_service.py "$@"
result=$?
if (( result != 0 )); then
  print "Recorder exited with status $result. Review the message above."
  read '?Press Return to close this window.'
fi
exit $result
