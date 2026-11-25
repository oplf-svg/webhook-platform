#!/usr/bin/env bash
# Everything that has to pass before something is merged. Needs the .NET SDK 10.0.4xx, Python 3.12 or newer, and Docker.
set -euo pipefail
cd "$(dirname "$0")/../.."

python3 .github/scripts/test_texts.py
bash .github/scripts/prepare.sh

echo "Two builds: one that is meant to be used, and one that can be told to die."
for part in api worker; do
  project="src/Platform.$([ "$part" = api ] && echo Api || echo Worker)"
  dotnet publish "$project" --configuration Release --verbosity quiet --artifacts-path build/r --output "build/release/$part"
  dotnet publish "$project" --configuration Release --verbosity quiet --artifacts-path build/f --output "build/faults/$part" -p:Faults=true
done

# A list of packages that is missing is made silently. In CI that has to be an error.
if [ "${CI:-}" = true ] && [ -n "$(git status --porcelain -- '*packages.lock.json')" ]; then
  echo "A packages.lock.json is missing or does not fit:"
  git status --porcelain -- '*packages.lock.json'
  exit 1
fi

echo "The tests in C#."
bash .github/scripts/quick.sh

echo "The tests that kill things."
cd crash
../build/venv/bin/python -m pytest -p no:cacheprovider -v test_steps.py
../build/venv/bin/python -m pytest -p no:cacheprovider -s test_storm.py
cd ..

# The image is made of what was tested just now, and tried. Not on a Mac: it would be for another kind of machine.
[ "${CI:-}" != true ] || bash .github/scripts/image_try.sh
