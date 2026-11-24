#!/usr/bin/env bash
# Builds the image from the files that tests.sh has made and tested. Needs Docker.
#
#   image.sh TAG [COMMIT]      for example: image.sh webhook-platform:try
#
# FAULTS=1 takes the test build, which can be told to die. Such an image is for a cluster that is thrown away. It is never
# published: image_look.py refuses it.
# PLATFORM is linux/amd64 if nothing is said. linux/arm64 can be built on any machine, because nothing is run in here.
set -euo pipefail
cd "$(dirname "$0")/../.."

tag="${1:?the tag is missing}"
commit="${2:-unknown}"
from=release
[ "${FAULTS:-0}" = 1 ] && from=faults

[ -d certificates ] || { echo "certificates/ is not there."; exit 1; }
# Whose it is stands in one place, and the image takes it from there.
owner="$(sed -n 's/^Copyright 20[0-9][0-9] //p' NOTICE)"
[ -n "$owner" ] || { echo "NOTICE does not say whose it is."; exit 1; }
rm -rf build/image
mkdir -p build/image
for part in api worker; do
  name="Platform.$([ "$part" = api ] && echo Api || echo Worker)"
  [ -f "build/$from/$part/$name.dll" ] || { echo "build/$from/$part is not there. Run tests.sh first."; exit 1; }
  cp -R "build/$from/$part" "build/image/$part"
  # The starter is a program for one kind of processor. The image starts the files with dotnet instead.
  rm -f "build/image/$part/$name" "build/image/$part/web.config"
done
cp -R certificates build/image/certificates
mkdir build/image/licenses
cp LICENSE NOTICE licenses/*.txt build/image/licenses/
# The user of the image has to be able to read everything, and may change nothing.
chmod -R u=rwX,go=rX build/image

DOCKER_BUILDKIT=1 docker build --file Dockerfile --platform "${PLATFORM:-linux/amd64}" --network none --provenance=false --sbom=false \
  --label "org.opencontainers.image.revision=$commit" --label "org.opencontainers.image.authors=$owner, https://github.com/oplf-svg" \
  --tag "$tag" build/image
