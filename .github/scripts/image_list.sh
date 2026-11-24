#!/usr/bin/env bash
# Writes the list of what is in the image (SPDX), and refuses a list that does not name what we know is inside.
#
#   image_list.sh IMAGE.tar LIST.json COMMIT
#
# Syft 1.57.1 of 9 November 2026. It gets no network, no right of the system and no disk to write to: it only reads the file, as
# whoever calls this, because "docker save" lets nobody else read it.
set -euo pipefail
cd "$(dirname "$0")/../.."

syft=ghcr.io/anchore/syft@sha256:03215d0e98b661d0ecbc8575dbc14d65be1743b5de5df6410aecb7e80f95b060
image="${1:?the image is missing}" list="${2:?the name of the list is missing}" commit="${3:?the commit is missing}"

docker run --rm --user "$(id -u):$(id -g)" --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --env HOME=/tmp --env SYFT_CHECK_FOR_APP_UPDATE=false --volume "$(cd "$(dirname "$image")" && pwd):/in:ro" "$syft" \
  scan "docker-archive:/in/$(basename "$image")" --output spdx-json --source-name webhook-platform --source-version "$commit" > "$list"
python3 .github/scripts/image_look.py "$image" --contents "$list"
