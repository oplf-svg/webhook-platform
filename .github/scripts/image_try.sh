#!/usr/bin/env bash
# Makes the image and tries everything that needs no right to write. tests.sh calls it on GitHub, in every pull request too, so
# that a mistake is seen there and not only on main. It leaves build/image.tar, which is what main publishes.
set -euo pipefail
cd "$(dirname "$0")/../.."

commit="${GITHUB_SHA:-$(git rev-parse HEAD)}"
python3 .github/scripts/test_image_look.py
python3 .github/scripts/test_image_workflow.py
bash .github/scripts/image.sh webhook-platform:new "$commit"
python3 .github/scripts/image_test.py webhook-platform:new
docker save --output build/image.tar webhook-platform:new
python3 .github/scripts/image_look.py build/image.tar "$commit"
bash .github/scripts/image_list.sh build/image.tar build/contents.spdx.json "$commit"
