# What the container promises

The API, the worker and the migration are one image, `ghcr.io/oplf-svg/webhook-platform`. From the first run of
[image.yml](../.github/workflows/image.yml) on, GitHub makes it for each commit on `main` and signs it. It is started by its
fingerprint, never by a name that somebody could move.

[image_test.py](../.github/scripts/image_test.py) starts it as a cloud has to, and checks what stands here.
[image_look.py](../.github/scripts/image_look.py) looks at what is in it. Both run in every pull request.

## What can be started

The entry point is always `/usr/bin/dotnet`. What comes after it says which program runs.

| | API | Worker | Migration |
|---|---|---|---|
| Words | `/app/api/Platform.Api.dll`, also when there are none | `/app/worker/Platform.Worker.dll` | the same, and `migrate` |
| Copies | as many as you like | as many as you like | one. A second one waits 2 seconds for the lock, fails and changes nothing |
| Port | 8080, HTTP without TLS. Something has to stand in front of it | none | none |
| Alive | `GET /health/live` answers 200 and asks nobody | it runs | its exit code |
| Ready | `GET /health/ready` answers 200, or 503 without the database, the tables or a right. 503 does not mean "start me again" | | |
| Asked to stop (SIGTERM) | It takes nothing new and finishes what it has, in at most 30 seconds | It finishes its events and takes no other one, in at most 15 seconds | It does not react and has to be killed. Everything is one transaction, so nothing is half done |
| Exit code 0 | It was asked to stop | It was asked to stop and has finished | Done, or there was nothing to do |
| Exit code 1 | | One of its loops broke, for example because there are no tables, or it did not finish in time | |
| Exit code 2 | | It does not know the words | It does not know the words |

Any other code means that it broke. The same crash can have another number on another kind of machine, so do not compare it
with one.

## Settings

They come from the environment. (ASP.NET would also read an `appsettings.json` in `/app`. There is none, and nothing can be
put there.) A setting that is missing, or a number that is wrong, stops the start, and the log names it and not its value.
Anything else that is wrong stops the start too, with the message of what could not read it. No secret of a feed is in it.

| Name | Who | What |
|---|---|---|
| `PLATFORM_DATABASE` | all | How to reach the database, as [Npgsql](https://www.npgsql.org/doc/connection-string-parameters.html) writes it. `Maximum Pool Size` belongs into it: each process may open 100 connections if nothing is said, and a small database does not have that many |
| `PLATFORM_FEEDS` | API | The path of the file with the feeds. It is read once, so other feeds need a new start |
| `PLATFORM_LOOPS`, `PLATFORM_LEASE_MS`, `PLATFORM_PAUSE_MS`, `PLATFORM_MAX_ATTEMPTS`, `PLATFORM_FIRST_DELAY_MS`, `PLATFORM_TROUBLE_DELAY_MS` | Worker | Whole numbers above 0. Without them 4, 30000, 250, 5, 1000 and 5000 |

## The box

- It runs as user and group 1654, with a disk that cannot be written to, without any right of the system. Nothing is written,
  not even to `/tmp`.
- There is no shell in it, no package manager and no other tool.
- Of ours it holds the two programs, the licences, and Amazon's public roots for databases in London
  (`/app/certificates/aws-rds-eu-west-2.pem`), which are in no public list.
- It has no check of its own (`HEALTHCHECK`): whoever runs it asks over HTTP.

## Logs

Each line on the standard output is one JSON object with `LogLevel`, `Category`, `EventId`, `Message` and `State`, and with
`Exception` in the same line if there is one. It has no time of its own. A crash is plain text on the standard error. No body,
signature or secret of a feed is in any of them, and no password, unless `PLATFORM_DATABASE` itself is not written as it should
be: then Npgsql's message can repeat a word of it.

## How it is made

1. The tests run. The image is made of the very files that they ran against: they are copied, and nothing is run while it is
   built. The base is Microsoft's ASP.NET image without a shell, at a fingerprint.
2. Another machine looks into it: base, settings, layers, files, packages against the lock files, licences, and no code for
   tests. It also writes a list of what is in it.
3. A third one puts exactly that file on GitHub's registry, if this commit has no image yet. A fourth one signs how it was
   built, and the list. These two run nothing of this repository.

Once the package is public, anybody can check an image, with its fingerprint and its commit:

```bash
gh attestation verify oci://ghcr.io/oplf-svg/webhook-platform@sha256:FINGERPRINT \
  --repo oplf-svg/webhook-platform --signer-workflow oplf-svg/webhook-platform/.github/workflows/image.yml \
  --source-ref refs/heads/main --source-digest COMMIT
```

`--repo` alone would not be enough, because any workflow of the repository could have signed.

## What it does not promise yet

- It is only for `linux/amd64`.
- The database needs a password in the setting. Signing in as the workload itself comes later.
- The API stops listening as soon as it is asked to stop. Whoever sends it requests has to know that first.
- If the database refuses the sign-in, the API answers 500 and the worker ends with 1.
- A misspelt setting is passed over without a word, and the normal value counts.
- There are no numbers to read (`/metrics`).
- It has run nowhere but on GitHub's machines.
