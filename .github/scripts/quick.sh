#!/usr/bin/env bash
# The tests in C#, against a database of their own. They take seconds, so this is the one to run while writing SQL.
# Words after it go to the tests, for example: quick.sh --filter-class Platform.Tests.InboxTests
set -euo pipefail
cd "$(dirname "$0")/../.."

name="quick-$RANDOM$RANDOM"
password="$(openssl rand -hex 16)"
port="$(python3 -c 'import socket; free = socket.socket(); free.bind(("127.0.0.1", 0)); print(free.getsockname()[1])')"
trap 'docker rm --force --volumes "$name" > /dev/null' EXIT
docker run --detach --name "$name" --env POSTGRES_PASSWORD="$password" --publish "127.0.0.1:$port:5432" "$(cat .github/postgres-image)" > /dev/null
ready=no
for _ in $(seq 120); do
  docker exec "$name" pg_isready --quiet --host 127.0.0.1 && ready=yes && break
  sleep 0.5
done
[ "$ready" = yes ] || { echo "The database did not start."; exit 1; }
PLATFORM_TEST_DATABASE="Host=127.0.0.1;Port=$port;Username=postgres;Password=$password;Database=postgres" \
  dotnet test --project tests/Platform.Tests --configuration Release "$@"
