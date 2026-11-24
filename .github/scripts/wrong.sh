#!/usr/bin/env bash
# On this branch only: not the mistakes, but many storms, each with another seed. "2/6" is the second of six parts of 150.
set -euo pipefail
cd "$(dirname "$0")/../.."

bash .github/scripts/prepare.sh
for part in api worker; do
  project="src/Platform.$([ "$part" = api ] && echo Api || echo Worker)"
  dotnet publish "$project" --configuration Release --verbosity quiet --artifacts-path build/f --output "build/faults/$part" -p:Faults=true
done
cd crash
../build/venv/bin/python storms.py "$1" "${STORMS:-150}"
