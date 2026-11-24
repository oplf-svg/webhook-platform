"""Proves that the tests can fail: puts one mistake at a time into a copy, and looks whether the tests notice it.

Each mistake names the groups that have to notice it, every time. The storm notices more of them now and then, but a check
that depends on luck would prove nothing. Every one of these mistakes is easy to make. The unchanged copy has to pass first,
because otherwise a failure would prove nothing.

    python wrong.py           all of them
    python wrong.py 2/4       the second of four parts
    python wrong.py NAME...   only these
"""

import dataclasses
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

from world import ROOT, Postgres

HERE = pathlib.Path(__file__).resolve().parent
# SQL that does not even run would fail for a reason that proves nothing: syntax, or something that does not exist.
NOT_EVEN_SQL = re.compile(r"\b(42601|42703|42P01|42883|42P18|42804|42704|42P10)\b")
# These say that a test did not get as far as looking. Nobody gives up and nobody says yes only when nothing was asked: each
# try ends by itself after 12 seconds.
NOT_A_FINDING = re.compile(r"still not: (the database answers|every fault has happened|every stopped process|a commit waits for|somebody gave up|\w+ is ready)")
# A small storm is enough to notice one mistake.
STORM = {"STORM_EVENTS": "250", "STORM_TIMES": "1", "STORM_ATTEMPTS": "8", "STORM_LONGEST": "120", "STORM_SETTLED": "30"}


@dataclasses.dataclass(frozen=True)
class Wrong:
    """One mistake: what it is, the groups of tests that have to notice it, and where it goes."""

    name: str
    what: str
    noticed_by: tuple[str, ...]
    file: str
    changes: tuple[tuple[str, str], ...] = ()
    # Instead of changes: a whole file. The right statements can be written in many ways, so the wrong ones are complete.
    text: str = ""
    # For the group "steps": which of them.
    steps: str = ""

    def put_into(self, text):
        if self.text:
            return self.text
        for old, new in self.changes:
            if text.count(old) != 1:
                raise ValueError(f"{self.name}: stands {text.count(old)} times in {self.file}: {old}")
            text = text.replace(old, new)
        return text


def wrong_sql():
    for path in sorted((HERE / "wrong").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        what, by = re.match(r"-- wrong: (.+)\n-- noticed by: (.+)\n", text).groups()
        # "steps:test_9" names the one that has to notice it.
        groups = dict(group.partition(":")[::2] for group in by.split())
        statement = path.name.split(".")[0]
        yield Wrong(path.stem, f"`{statement}.sql` {what}", tuple(groups), f"db/{statement}.sql", text=text, steps=groups.get("steps", ""))


def wrong_code():
    quick, steps, storm = "quick", "steps", "storm"
    signature, envelope, feeds = "src/Platform.Core/Signature.cs", "src/Platform.Core/Envelope.cs", "src/Platform.Core/Feeds.cs"
    receiver, api, worker = "src/Platform.Api/Receiver.cs", "src/Platform.Api/Api.cs", "src/Platform.Worker/EventWorker.cs"
    settings, database, ledger = "src/Platform.Worker/WorkerSettings.cs", "src/Platform.Data/Database.cs", "src/Platform.Data/Ledger.cs"

    def change(name, what, noticed_by, file, *changes, steps=""):
        return Wrong(name, what, noticed_by, file, changes, steps=steps)

    mark = "        if (!await inbox.FinishAsync(claim, connection, transaction))\n            return false;\n"
    after_mark, effect = '        Fault.At("worker.after_mark");\n', "        await ledger.ApplyAsync(claim, order, connection, transaction);\n"
    after_effect, commit = '        Fault.At("worker.after_effect");\n', "        await transaction.CommitAsync();\n"
    transaction = mark + after_mark + effect + after_effect + commit
    again = "        await using var second = await connection.BeginTransactionAsync();\n"
    unreadable = "            await GiveBackAsync(claim, problem.Message, TimeSpan.Zero, forGood: true);\n"

    return [
        # Who is let in.
        change("signature.every_one", "Every signature is right", (quick, storm), signature, ("var right = false;", "var right = true;")),
        change("signature.only_the_start", "Only the first bytes of a signature are compared", (quick,), signature,
               ("FixedTimeEquals(wanted, signature)", "FixedTimeEquals(wanted.AsSpan(0, 8), signature.AsSpan(0, 8))")),
        change("signature.any_length", "A cut signature is compared as far as it goes", (quick, storm), signature,
               ("CryptographicOperations.FixedTimeEquals(wanted, signature)", "wanted.AsSpan().StartsWith(signature)"),
               ("                && length == signature.Length)\n                offered.Add(signature);", ")\n                offered.Add(signature[..length]);")),
        change("signature.any_kind", "A signature of another kind counts as ours", (quick, storm), signature,
               ("part.StartsWith(Version, StringComparison.Ordinal)", "part.Contains(',', StringComparison.Ordinal)"), ("part[Version.Length..]", "part[(part.IndexOf(',', StringComparison.Ordinal) + 1)..]")),
        change("time.any", "The time is not checked", (quick, storm), envelope, ("Math.Abs(distance) <= (long)Limits.Clock.TotalSeconds", "Math.Abs(distance) >= 0 * (long)Limits.Clock.TotalSeconds")),
        change("time.any_in_the_future", "A time in the future is fine", (quick, storm), envelope, ("Math.Abs(distance)", "distance")),
        change("time.an_hour", "A recorded request is good for an hour", (quick, storm), "src/Platform.Core/Limits.cs", ("FromMinutes(5)", "FromHours(1)")),
        change("time.before_the_body", "The time is only checked before the body has come", (quick,), receiver,
               ("        // Once more, because a body can take its time.\n        if (!envelope.IsCurrent(clock.GetUtcNow()))", "        if (body.Length < 0)")),
        change("id.with_a_dot", "An id may have a dot, so signed bytes can be cut in two ways", (quick, storm), envelope, ("c is '_' or ':' or '-'", "c is '_' or ':' or '-' or '.'")),
        change("header.first_of_several", "Of a header that is there twice, the first one counts", (quick,), envelope, ("id is not [{ } theId]", "id is not [{ } theId, ..]")),
        change("feeds.any_secret", "A feed can sign for another feed", (quick, storm), feeds,
               ("=> _feeds.GetValueOrDefault(name, _nobody);", "=> _feeds.TryGetValue(name, out var feed) ? feed with { Secrets = [.. _feeds.Values.SelectMany(one => one.Secrets)] } : _nobody;")),
        change("feeds.the_first_for_nobody", "A feed that does not exist is taken for another one", (quick, storm), feeds, ("_feeds.GetValueOrDefault(name, _nobody)", "_feeds.GetValueOrDefault(name, _feeds.Values[0] with { Shop = _nobody.Shop })")),
        change("feeds.nobody_has_a_known_secret", "The secrets that nobody should know are always the same", (quick,), feeds, ("RandomNumberGenerator.GetBytes(32)", "new byte[RandomNumberGenerator.GetInt32(32, 33)]")),
        change("feeds.fewer_secrets", "How long an answer takes tells how many secrets a feed has", (quick,), feeds, ("SecretsOfEach - secrets.Count", "Math.Min(0, SecretsOfEach - secrets.Count) + (secrets.Count == 0 ? 1 : 0)")),
        change("feeds.zeros_make_another_secret", "A secret with a zero at its end counts as another one", (quick,), feeds, (".TrimEnd((byte)0)", ".TrimEnd((byte)0) is var _ ? secret : secret")),
        change("feeds.shared_secret", "Two feeds may have the same secret", (quick,), feeds, ("secrets.Distinct().Count() != secrets.Count", "secrets.Distinct().Count() > secrets.Count")),
        change("receive.tells_who_exists", "The answer tells which feeds exist", (quick, storm), receiver,
               ("        if (!Signature.IsRight(", "        if (known.Shop.Length == 0)\n            return Answer(StatusCodes.Status404NotFound, \"unknown\");\n        if (!Signature.IsRight(")),
        change("receive.too_much", "A body may be four times as large", (quick, storm), "src/Platform.Core/Limits.cs", ("256 * 1024", "1024 * 1024")),
        change("receive.only_its_length", "Without a length, a body can have any size", (quick,), api, ("server.Limits.MaxRequestBodySize = Limits.BodyBytes;", "server.Limits.MaxRequestBodySize = null;")),
        # What is kept.
        change("refuse.in_silence", "Nobody hears that a feed is refused", (quick, steps), "src/Platform.Api/Refusals.cs",
               ("            Refused(_log, feed, number, reason);", "            _ = (_log, feed, number, reason);"), steps="test_1_forged"),
        change("refuse.forgets_the_rest", "The last refusals are never said", (quick,), "src/Platform.Api/Refusals.cs", ("            Say(feed, reason, count);\n    }\n\n    void Say", "            _ = (feed, reason, count);\n    }\n\n    void Say")),
        change("refuse.a_line_for_each", "Anybody can fill the log", (quick, steps), "src/Platform.Api/Refusals.cs", ("clock.GetElapsedTime(last) < Pause", "clock.GetElapsedTime(last) < -Pause"), steps="test_1_forged"),
        change("receive.stores_first", "Everything is stored, and only then checked", (quick, storm), receiver,
               ("        if (!Signature.IsRight(", "        await inbox.StoreAsync(feed, envelope.Id, known.Shop, body);\n        if (!Signature.IsRight(")),
        change("receive.yes_before_it_is_stored", "It says yes before the database does", (quick, storm), receiver,
               ("stored = await inbox.StoreAsync(known.Name, envelope.Id, known.Shop, body);", "stored = Stored.New;\n            _ = inbox.StoreAsync(known.Name, envelope.Id, known.Shop, body);\n            await Task.Yield();")),
        change("receive.yes_without_a_database", "Without the database it still says yes", (quick, storm), receiver,
               ('Answer(StatusCodes.Status503ServiceUnavailable, "not stored")', 'Answer(StatusCodes.Status202Accepted, "stored")')),
        change("receive.as_text", "The body is read as text", (quick, storm), receiver, ("body = buffer.ToArray();", "body = System.Text.Encoding.UTF8.GetBytes(System.Text.Encoding.UTF8.GetString(buffer.ToArray()));")),
        change("receive.logs_the_body", "The body is written into the log", (steps, storm), receiver,
               ("        Took(log, stored ==", "        var said = envelope.Id + System.Text.Encoding.UTF8.GetString(body);\n        Took(log, stored =="), ("known.Name, envelope.Id, stored);", "known.Name, said, stored);"), steps="nothing_wrong"),
        change("database.cancels_a_commit", "A commit that waits for the standby is cancelled, and that is taken for a yes", (steps,), database, ("CancellationTimeout = -1;", "CancellationTimeout = 2000;"), steps="test_16"),
        change("database.waits_for_ever", "A database that hangs is waited for", (steps,), database, ("CommandTimeout = 8;", "CommandTimeout = 0;"), steps="test_11_the_database_hangs_on_an_open_connection"),
        change("database.yes_before_the_disk", "The database says yes before it is on the disk", (steps,), database, ("-c statement_timeout=5s", "-c synchronous_commit=off -c statement_timeout=5s"), steps="test_11"),
        # What is done with it.
        change("work.effect_before_the_mark", "The effect is made first, and marked in a second transaction", (steps, storm), worker,
               (transaction, effect + commit + after_effect + again + mark.replace("transaction)", "second)") + after_mark + commit.replace("transaction", "second")), steps="test_7"),
        change("work.mark_before_the_effect", "It is marked first, and the effect is made in a second transaction", (steps, storm), worker,
               (transaction, mark + commit + after_mark + again + effect.replace("transaction)", "second)") + after_effect + commit.replace("transaction", "second")), steps="test_7"),
        change("work.ignores_the_loss", "A worker goes on although it lost its claim", (steps,), worker, (mark, "        await inbox.FinishAsync(claim, connection, transaction);\n"), steps="test_8"),
        change("work.never_commits", "Nothing is ever committed", (quick,), worker, (commit, "        await transaction.RollbackAsync();\n")),
        change("work.gives_up_at_once", "A step that fails once is not tried again", (quick,), worker, ("settings.DelayAfter(claim.Attempt), forGood: false)", "settings.DelayAfter(claim.Attempt), forGood: true)")),
        change("work.trouble_counts", "Trouble with the database counts against the event", (quick, steps), worker, ("forGood: false, counts: false", "forGood: false, counts: true"), steps="test_14"),
        change("work.all_loops_are_careless", "Every loop takes what may have killed its worker", (steps,), worker, ("LoopAsync(number == 0, stop)", "LoopAsync(number >= 0, stop)"), steps="test_12"),
        change("work.no_loop_is_careful", "No loop takes what a dead worker had", (steps, storm), worker, ("LoopAsync(number == 0, stop)", "LoopAsync(number < 0, stop)"), steps="test_7"),
        change("work.aside_in_silence", "Nobody hears what is set aside", (steps,), worker, ("                SetAside(log, aside.Feed, aside.EventId, aside.Attempts, aside.Reason);", "                _ = aside;"), steps="test_12"),
        change("work.everything_is_trouble", "A mistake is waited out as if it was an outage", (quick, steps), "src/Platform.Data/Trouble.cs",
               ('code[..2] is "08" or "53" or "57" or "58" || code is "40001" or "40P01" or "55P03" or "55006"', "code.Length > 0"), steps="test_17"),
        change("work.nothing_is_trouble", "An outage ends the worker and counts against its events", (quick, steps), "src/Platform.Data/Trouble.cs",
               ('code[..2] is "08" or "53" or "57" or "58" || code is "40001" or "40P01" or "55P03" or "55006"', "code.Length == 0"), steps="test_11"),
        change("work.drops_the_unreadable", "What cannot be understood is marked as done", (quick, steps, storm), worker,
               (unreadable, "            await using var connection = await source.OpenConnectionAsync();\n            await using var transaction = await connection.BeginTransactionAsync();\n"
                            "            await inbox.FinishAsync(claim, connection, transaction);\n            await transaction.CommitAsync();\n            NoDatabase(log, problem);\n"), steps="test_13"),
        change("work.tries_the_unreadable_again", "What cannot be understood is tried again and again", (quick, steps), worker, (unreadable, unreadable.replace("true", "false")), steps="test_13"),
        change("work.tries_the_known_order_again", "An order that is already there is tried again and again", (quick,), worker,
               ('"the order is already there", TimeSpan.Zero, forGood: true', '"the order is already there", TimeSpan.Zero, forGood: false')),
        change("work.does_not_stop", "Asked to stop, it takes the next event", (steps,), worker, ("while (!stop.IsCancellationRequested)", "while (settings.Loops > 0)"), steps="test_15"),
        change("work.goes_on_with_less", "When one of its loops breaks, a worker goes on with the others", (steps,), worker, ("await stop.CancelAsync();", "await Task.Yield();"), steps="says_so"),
        change("work.same_moment", "Events that met trouble come back together", (quick,), settings, ("TroubleDelay * (1 + Random.Shared.NextDouble())", "TroubleDelay * (2 - Random.Shared.Next(1))")),
        change("work.same_wait", "Each wait is as short as the first", (quick,), settings, ("Math.Pow(2, Math.Clamp(attempt, 1, 12) - 1)", "Math.Pow(1, Math.Clamp(attempt, 1, 12) - 1)")),
        change("database.never_lets_go", "A stopped worker holds its rows for ever", (steps,), database, (" -c idle_in_transaction_session_timeout=5s -c transaction_timeout=10s", ""), steps="test_9"),
        change("effect.forgets_the_sum", "The total is only the last amount", (quick, storm), "db/effect.sql", ("total.cents + excluded.cents", "excluded.cents")),
        change("effect.no_safety_net", "There is no safety net", (quick,), "db/002_orders.sql", ("constraint orders_once unique (shop, feed, number)", "constraint orders_once check (shop <> number)")),
        change("effect.one_net_for_all_shops", "Two shops cannot use the same number", (quick,), "db/002_orders.sql", ("unique (shop, feed, number)", "unique (number)")),
        change("effect.one_net_for_all_channels", "Two channels of a shop cannot use the same number", (quick, steps), "db/002_orders.sql", ("unique (shop, feed, number)", "unique (shop, number)"), steps="test_18"),
        change("effect.a_net_for_each_event", "An order counts again when it comes with another id", (quick, steps), "db/002_orders.sql", ("unique (shop, feed, number)", "unique (shop, feed, number, event_id)"), steps="test_18"),
        change("effect.shop_from_the_text", "The event says whose it is", (quick,), ledger,
               ('Add("shop", NpgsqlDbType.Text, claim.Shop);', 'Add("shop", NpgsqlDbType.Text, System.Text.Json.JsonDocument.Parse(claim.Body).RootElement.TryGetProperty("shop", out var shop) ? shop.GetString()! : claim.Shop);')),
        change("order.twice_the_same_name", "Of a name that stands twice, the last one counts", (quick,), "src/Platform.Core/Order.cs", ("AllowDuplicateProperties = false", "AllowDuplicateProperties = true")),
        change("order.another_end", "Something comes out of reading that is neither an order nor unreadable", (quick,), "src/Platform.Core/Order.cs", ("problem is JsonException or InvalidOperationException", "problem is JsonException")),
        change("order.repeats_what_it_got", "A reason repeats what stood in the event", (quick,), "src/Platform.Core/Order.cs", ('throw new UnreadableException("not a type that is known")', 'throw new UnreadableException("not a type that is known: " + Text(root, "type"))')),
        # How it is run.
        change("migrate.takes_any_change", "A file of tables may change after it was applied", (quick,), "src/Platform.Data/Migrator.cs", ("else if (known != hash)", "else if (known.Length == 0)")),
        change("ready.without_its_tables", "It says that it is ready without its tables", (quick,), "src/Platform.Data/Inbox.cs", ("select exists (select from inbox)", "select 1")),
        change("ready.without_a_database", "It says that it is ready without a database", (quick,), api, ("return Results.StatusCode(StatusCodes.Status503ServiceUnavailable);", "return Results.Ok();")),
        change("build.faults_for_everybody", "The build for production can be killed by a setting", (steps,), "Directory.Build.props",
               ("    <TargetFramework>net10.0</TargetFramework>\n", "    <TargetFramework>net10.0</TargetFramework>\n    <Faults Condition=\"'$(Faults)' == ''\">true</Faults>\n"), steps="production"),
    ]


class Copy:
    """A copy of everything that is or could be committed. The mistakes go there and never into the original."""

    def __init__(self, folder, python):
        self.folder, self.python, self.last = folder, python, ""
        names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout.decode().split("\0")
        for name in filter(None, names):
            if (ROOT / name).is_file():
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, folder / name)
        # The copy of the crash tests starts with the environment of this one.
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith(("PLATFORM_", "GITHUB_STEP_SUMMARY"))} | {"DOTNET_NOLOGO": "1"}

    def run(self, words, where=".", **more):
        done = subprocess.run(words, cwd=self.folder / where, env=self.environment | more, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=900)
        self.last = done.stdout
        return done.returncode, done.stdout

    def build(self, *what):
        for part, faults in what:
            out = [] if part == "tests" else ["--output", f"build/{'faults' if faults else 'release'}/{part}"]
            project = "tests/Platform.Tests" if part == "tests" else f"src/Platform.{part.capitalize()}"
            code, text = self.run(["dotnet", "build" if part == "tests" else "publish", project, "--configuration", "Release", "--verbosity", "quiet", "--artifacts-path", f"build/{'f' if faults else 'r'}",
                                   *(["-p:Faults=true"] if faults else []), *out])
            if code:
                raise ValueError("it does not build:\n" + "\n".join(sorted({line for line in text.split("\n") if "error" in line})[:5]))

    def test(self, group, postgres, steps="", sql=None):
        """Runs one group of tests. Returns None if all passed, and otherwise what noticed the mistake."""
        more = {"PLATFORM_SQL": str(sql)} if sql else {}
        if group == "quick":
            more["PLATFORM_TEST_DATABASE"] = postgres.dotnet("postgres", "postgres", postgres.password)
            code, text = self.run(["dotnet", "build/f/bin/Platform.Tests/release/Platform.Tests.dll", "-noColor", "-stopOnFail"], **more)
            # Not in the log, because one of the tests is about a table that is not there.
            said = "\n".join(line for line in text.split("\n") if not line.startswith("{"))
            if sql and NOT_EVEN_SQL.search(said):
                raise ValueError("the SQL does not even run: " + NOT_EVEN_SQL.search(said).group())
            failed = re.findall(r"Platform\.Tests\.(\w+)\.(\w+).*\[FAIL\]", text)
            if bool(failed) != bool(code) or (not failed and not re.search(r"Total: [1-9]\d\d, Errors: 0, Failed: 0, Skipped: 0", text)):
                raise ValueError(f"the tests did not run as they should:\n{text[-2000:]}")
            return f"{failed[0][0]}: {failed[0][1].replace('_', ' ')}" if failed else None
        words = [self.python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--tb=short", "--no-header"]
        words += ["test_storm.py", "-s"] if group == "storm" else ["test_steps.py", *(["-k", steps] if steps else [])]
        code, text = self.run(words, "crash", **more, **(STORM if group == "storm" else {}))
        if code == 0:
            return None
        # A test that broke has noticed nothing. Only a test that looked and saw something wrong counts.
        why = re.findall(r"^E +(?:AssertionError: )?(after \d+ seconds still not: .*|assert .*)$", text, re.MULTILINE)
        failed = re.search(r"^FAILED test_\w+\.py::(.+?)(?: - .*)?$", text, re.MULTILINE)
        if code != 1 or not failed or not why or NOT_A_FINDING.search(why[-1]):
            raise ValueError(f"the tests did not run as they should:\n{text[-3000:]}")
        if group == "storm":
            counted = re.findall(r"^\| \*\*(.+?)\*\* \| ([1-9]\d*) \|$", text, re.MULTILINE)
            return f"the storm ({', '.join(f'{what.lower()}: {times}' for what, times in counted) or why[-1][:90]})"
        # With the reason, so that everybody can see if it is the right one.
        return f"{failed.group(1).replace('_', ' ')}: {why[-1][:90]}".replace("|", "/")


def chosen(words):
    everything = [*wrong_sql(), *wrong_code()]
    if not words:
        return everything
    if part := re.fullmatch(r"(\d+)/(\d+)", words[0]):
        number, of = int(part[1]), int(part[2])
        return everything[number - 1::of]
    unknown = set(words) - {wrong.name for wrong in everything}
    if unknown:
        raise SystemExit(f"not known: {', '.join(sorted(unknown))}")
    return [wrong for wrong in everything if wrong.name in words]


def main(words):
    todo = chosen(words)
    rows, missed = [], []
    postgres = Postgres()
    with tempfile.TemporaryDirectory(prefix="wrong-") as folder:
        try:
            postgres.start()
            copy = Copy(pathlib.Path(folder).resolve(), sys.executable)
            everything = [(part, faults) for faults in (True, False) for part in ("api", "worker")]
            copy.build(("tests", True), *everything)
            for group in ("quick", "steps", "storm"):
                noticed = copy.test(group, postgres)
                if noticed:
                    raise SystemExit(f"The unchanged copy already fails, so nothing can be proven. {noticed}\n{copy.last[-4000:]}")
            print("The unchanged copy passes.", flush=True)

            # The wrong SQL first, because it needs no new build.
            for wrong in sorted(todo, key=lambda one: not one.text):
                start = time.monotonic()
                path = copy.folder / wrong.file
                before = path.read_text(encoding="utf-8")
                try:
                    sql = None
                    if wrong.text:
                        sql = copy.folder / "build" / "sql"
                        shutil.rmtree(sql, ignore_errors=True)
                        shutil.copytree(copy.folder / "db", sql)
                        (sql / path.name).write_text(wrong.put_into(before), encoding="utf-8")
                    else:
                        path.write_text(wrong.put_into(before), encoding="utf-8")
                        release = [(part, False) for part in ("api", "worker")] if "production" in wrong.steps else []
                        copy.build(*([("tests", True)] if "quick" in wrong.noticed_by else []), *([("api", True), ("worker", True)] if wrong.noticed_by != ("quick",) else []), *release)
                    for group in wrong.noticed_by:
                        noticed = copy.test(group, postgres, wrong.steps, sql)
                        rows.append((wrong.what, noticed or f"**nothing in {group}**"))
                        if not noticed:
                            missed.append(f"{wrong.name}: {group} did not notice it")
                except ValueError as problem:
                    rows.append((wrong.what, f"**not valid**: {str(problem).splitlines()[0]}"))
                    missed.append(f"{wrong.name}: {problem}")
                finally:
                    path.write_text(before, encoding="utf-8")
                print(f"{wrong.name}: {'; '.join(noticed for what, noticed in rows if what == wrong.what)} ({time.monotonic() - start:.0f} s)", flush=True)
        finally:
            postgres.remove()

    text = f"### {len(todo)} mistakes, one at a time\n\n| Mistake | Noticed by |\n|---|---|\n" + "".join(f"| {what} | {noticed} |\n" for what, noticed in rows)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as file:
            file.write(text + "\n")
    print(f"\n{len(todo)} mistakes, {len(missed)} of them not noticed as they should be.")
    for line in missed:
        print(line)
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
