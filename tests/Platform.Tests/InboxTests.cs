using Npgsql;
using Platform.Data;
using Xunit;

namespace Platform.Tests;

/// <summary>What the five statements of db/ have to hold, whoever else runs at the same moment. See docs/contract.md.</summary>
public sealed class InboxTests(TestDatabase database) : IClassFixture<TestDatabase>, IAsyncLifetime
{
    static readonly TimeSpan Long = TimeSpan.FromMinutes(1);
    static readonly TimeSpan Short = TimeSpan.FromMilliseconds(80);
    static readonly byte[] Body = Sender.Order("N-1");
    static readonly byte[] Other = Sender.Order("N-2");

    readonly NpgsqlDataSource _source = Database.Open(database.Connection, "test");
    Inbox _inbox = null!;

    public async ValueTask InitializeAsync()
    {
        _inbox = new Inbox(_source);
        await database.RunAsync("truncate inbox, conflicts, orders, totals");
    }

    public ValueTask DisposeAsync() => _source.DisposeAsync();

    Task<Stored> Store(string id = "e1", byte[]? body = null, string feed = "north-web") => _inbox.StoreAsync(feed, id, "north", body ?? Body);

    Task<Claim?> Claim(TimeSpan? lease = null, int most = 5, bool onlyWaiting = false) => _inbox.ClaimAsync(lease ?? Long, most, onlyWaiting);

    async Task<List<string>> SetAside(int most = 5) => [.. (await _inbox.SetAsideAsync(most)).Select(aside => $"{aside.Feed} {aside.EventId} {aside.Attempts} {aside.Reason}").Order(StringComparer.Ordinal)];

    async Task<bool> Finish(Claim claim, bool commit = true)
    {
        await using var connection = await _source.OpenConnectionAsync();
        await using var transaction = await connection.BeginTransactionAsync();
        var finished = await _inbox.FinishAsync(claim, connection, transaction);
        if (commit)
            await transaction.CommitAsync();
        return finished;
    }

    Task<string?> GiveBack(Claim claim, TimeSpan? delay = null, bool forGood = false, bool counts = true, int most = 5) =>
        _inbox.GiveBackAsync(claim, "a reason", delay ?? TimeSpan.Zero, forGood, counts, most);

    /// <summary>One row as it is seen from outside: state, attempts, deliveries, reason.</summary>
    Task<string> Row(string id = "e1") => database.AskAsync<string>(
        "select concat_ws(' ', state, attempts, deliveries, coalesce(reason, '-')) from inbox where event_id = $1", id);

    // Storing.

    [Fact]
    public async Task A_new_event_is_stored_as_it_came()
    {
        Assert.Equal(Stored.New, await Store());
        Assert.Equal("waiting 0 1 -", await Row());
        Assert.Equal(Body, await database.AskAsync<byte[]>("select body from inbox where shop = 'north' and feed = 'north-web'"));
    }

    [Fact]
    public async Task The_same_event_again_is_only_counted()
    {
        await Store();
        Assert.Equal(Stored.Already, await Store());
        Assert.Equal(Stored.Already, await Store());
        Assert.Equal("waiting 0 3 -", await Row());
        Assert.Equal(0, await database.AskAsync<long>("select count(*) from conflicts"));
    }

    [Fact]
    public async Task An_event_that_is_done_stays_done_when_it_comes_again()
    {
        await Store();
        Assert.True(await Finish((await Claim())!));
        Assert.Equal(Stored.Already, await Store());
        Assert.Equal("done 1 2 -", await Row());
        Assert.Null(await Claim());
    }

    [Fact]
    public async Task An_event_that_is_worked_on_stays_with_its_worker_when_it_comes_again()
    {
        await Store();
        var claim = (await Claim())!;
        Assert.Equal(Stored.Already, await Store());
        Assert.Null(await Claim());
        Assert.True(await Finish(claim));
    }

    [Fact]
    public async Task Another_body_under_a_used_id_is_kept_beside_the_first()
    {
        await Store();
        Assert.Equal(Stored.Conflict, await Store(body: Other));
        Assert.Equal(Stored.Conflict, await Store(body: Other));
        Assert.Equal(Stored.Already, await Store());
        Assert.Equal("waiting 0 2 -", await Row());
        Assert.Equal(Body, await database.AskAsync<byte[]>("select body from inbox"));
        Assert.Equal(Other, Assert.Single(await database.ListAsync<byte[]>("select distinct body from conflicts where feed = 'north-web' and event_id = 'e1'")));
    }

    [Fact]
    public async Task Every_other_body_is_kept_and_not_only_the_first()
    {
        var bodies = Enumerable.Range(0, 5).Select(number => Sender.Order($"N-{number}")).ToList();
        foreach (var (body, number) in bodies.Select((body, number) => (body, number)))
            Assert.Equal(number == 0 ? Stored.New : Stored.Conflict, await Store(body: body));
        var kept = await database.ListAsync<byte[]>("select distinct body from conflicts order by body");
        Assert.Equal(bodies.Skip(1).Select(Convert.ToHexString), kept.Select(Convert.ToHexString));
    }

    [Fact]
    public async Task Another_body_for_an_event_that_is_done_does_not_wake_it()
    {
        await Store();
        Assert.True(await Finish((await Claim())!));
        Assert.Equal(Stored.Conflict, await Store(body: Other));
        Assert.StartsWith("done 1", await Row(), StringComparison.Ordinal);
        Assert.Null(await Claim());
    }

    [Fact]
    public async Task The_same_id_in_two_feeds_is_two_events()
    {
        Assert.Equal(Stored.New, await Store());
        Assert.Equal(Stored.New, await Store(feed: "north-app", body: Other));
        Assert.Equal(2, await database.AskAsync<long>("select count(*) from inbox"));
    }

    [Fact]
    public async Task A_body_can_be_any_bytes()
    {
        byte[][] bodies = [[], [0], [0xff, 0xfe, 0], [.. Enumerable.Range(0, 256 * 1024).Select(i => (byte)i)]];
        foreach (var (body, number) in bodies.Select((body, number) => (body, number)))
        {
            Assert.Equal(Stored.New, await Store($"b{number}", body));
            Assert.Equal(Stored.Already, await Store($"b{number}", body));
            Assert.Equal(body, await database.AskAsync<byte[]>("select body from inbox where event_id = $1", $"b{number}"));
        }
    }

    [Fact]
    public async Task Of_many_that_store_the_same_event_at_once_one_is_the_first()
    {
        for (var round = 0; round < 25; round++)
        {
            var id = $"r{round}";
            var results = await Task.WhenAll(Enumerable.Range(0, 16).Select(_ => Task.Run(() => Store(id))));
            Assert.Single(results, Stored.New);
            Assert.Equal(15, results.Count(result => result == Stored.Already));
            Assert.Equal("waiting 0 16 -", await Row(id));
        }
    }

    [Fact]
    public async Task Of_two_bodies_at_once_one_wins_and_everybody_hears_the_same()
    {
        for (var round = 0; round < 25; round++)
        {
            var id = $"r{round}";
            var results = await Task.WhenAll(Enumerable.Range(0, 16).Select(i => Task.Run(() => Store(id, i % 2 == 0 ? Body : Other))));
            Assert.Single(results, Stored.New);
            Assert.Equal(7, results.Count(result => result == Stored.Already));
            Assert.Equal(8, results.Count(result => result == Stored.Conflict));
            var kept = await database.AskAsync<byte[]>("select body from inbox where event_id = $1", id);
            var beside = Assert.Single(await database.ListAsync<byte[]>("select distinct body from conflicts where event_id = $1", id));
            Assert.NotEqual(kept, beside);
        }
    }

    [Fact]
    public async Task Storing_waits_for_a_twin_that_is_not_decided_yet()
    {
        await using var connection = await _source.OpenConnectionAsync();
        await using var transaction = await connection.BeginTransactionAsync();
        await using (var first = new NpgsqlCommand(await File.ReadAllTextAsync(StoreFile()), connection, transaction))
        {
            first.Parameters.AddWithValue("feed", "north-web");
            first.Parameters.AddWithValue("event_id", "e1");
            first.Parameters.AddWithValue("shop", "north");
            first.Parameters.AddWithValue("body", Body);
            await first.ExecuteNonQueryAsync();
        }

        var second = Store();
        await Task.Delay(200);
        Assert.False(second.IsCompleted);
        // The first one is taken back, so the second one is the one that counts.
        await transaction.RollbackAsync();
        Assert.Equal(Stored.New, await second);
    }

    // Claiming.

    [Fact]
    public async Task With_nothing_there_nothing_is_claimed() => Assert.Null(await Claim());

    [Fact]
    public async Task A_claim_has_all_that_is_needed_and_is_counted_before_the_work()
    {
        await Store();
        var claim = (await Claim())!;
        Assert.Equal(("north-web", "e1", "north", 1), (claim.Feed, claim.EventId, claim.Shop, claim.Attempt));
        Assert.Equal(Body, claim.Body);
        Assert.Equal("working 1 1 -", await Row());
    }

    [Fact]
    public async Task While_a_claim_holds_nobody_else_gets_the_event()
    {
        await Store();
        Assert.NotNull(await Claim());
        Assert.Null(await Claim());
    }

    [Fact]
    public async Task A_claim_ends_by_itself()
    {
        await Store();
        var first = (await Claim(Short))!;
        await Task.Delay(Short * 2);
        var second = (await Claim())!;
        Assert.Equal(("e1", 2), (second.EventId, second.Attempt));
        Assert.NotEqual(first.Token, second.Token);
        Assert.Null(await Claim());
    }

    [Fact]
    public async Task A_claim_that_ended_is_not_for_those_who_take_only_what_waits()
    {
        await Store("died");
        Assert.NotNull(await Claim(Short));
        await Task.Delay(Short * 2);
        Assert.Null(await Claim(onlyWaiting: true));
        await Store("waits");
        Assert.Equal("waits", (await Claim(onlyWaiting: true))!.EventId);
        Assert.Equal("died", (await Claim())!.EventId);
    }

    [Fact]
    public async Task What_was_given_back_is_for_everybody()
    {
        await Store();
        await GiveBack((await Claim())!);
        Assert.Equal(2, (await Claim(onlyWaiting: true))!.Attempt);
    }

    // Setting aside.

    [Fact]
    public async Task An_event_that_ends_every_worker_is_set_aside_and_named()
    {
        await Store("bad");
        for (var attempt = 1; attempt <= 3; attempt++)
        {
            Assert.Empty(await SetAside(most: 3));
            Assert.Equal(attempt, (await Claim(Short, most: 3))!.Attempt);
            await Task.Delay(Short * 2);
        }

        Assert.Null(await Claim(most: 3));
        Assert.StartsWith("north-web bad 3 ", Assert.Single(await SetAside(most: 3)), StringComparison.Ordinal);
        Assert.Empty(await SetAside(most: 3));
        var row = await Row("bad");
        Assert.StartsWith("aside 3 1 ", row, StringComparison.Ordinal);
        Assert.DoesNotContain(" -", row, StringComparison.Ordinal);
        Assert.Null(await Claim(most: 99));
    }

    [Fact]
    public async Task An_event_that_has_one_try_left_gets_it()
    {
        await Store();
        Assert.NotNull(await Claim(Short, most: 2));
        await Task.Delay(Short * 2);
        Assert.Empty(await SetAside(most: 2));
        Assert.Equal(2, (await Claim(Short, most: 2))!.Attempt);
        // Still worked on, so not set aside yet.
        Assert.Empty(await SetAside(most: 2));
        Assert.StartsWith("working 2", await Row(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task One_that_has_no_try_left_does_not_hold_up_the_next()
    {
        await Store("bad");
        Assert.NotNull(await Claim(Short, most: 1));
        await Task.Delay(Short * 2);
        await Store("good");
        Assert.Equal("good", (await Claim(most: 1))!.EventId);
    }

    [Fact]
    public async Task With_fewer_attempts_allowed_than_before_nothing_waits_for_ever()
    {
        await Store();
        Assert.Equal("waiting", await GiveBack((await Claim(most: 5))!, most: 5));
        Assert.Equal("waiting", await GiveBack((await Claim(most: 5))!, most: 5));
        Assert.Null(await Claim(most: 2));
        Assert.Equal(["north-web e1 2 a reason"], await SetAside(most: 2));
        Assert.Equal("aside 2 1 a reason", await Row());
    }

    [Fact]
    public async Task Only_what_is_due_and_used_up_is_set_aside()
    {
        await Store("fresh");
        await Store("later");
        await Store("done");
        await Store("tried");
        await Store("used-up");
        foreach (var id in new[] { "fresh", "later", "done", "tried", "used-up" })
            Assert.Equal(id, (await Claim(Short, most: 9))!.EventId);
        await Task.Delay(Short * 2);
        await database.RunAsync("update inbox set state = 'waiting', token = null, attempts = 0 where event_id = 'fresh'");
        await database.RunAsync("update inbox set state = 'waiting', token = null, attempts = 7, reason = 'x', due_at = now() + interval '1 hour' where event_id = 'later'");
        await database.RunAsync("update inbox set state = 'done', token = null, attempts = 7 where event_id = 'done'");
        await database.RunAsync("update inbox set attempts = 4 where event_id = 'tried'");
        await database.RunAsync("update inbox set attempts = 5 where event_id = 'used-up'");
        Assert.StartsWith("north-web used-up 5 ", Assert.Single(await SetAside()), StringComparison.Ordinal);
        Assert.Equal(1, await database.AskAsync<long>("select count(*) from inbox where state = 'aside'"));
    }

    [Fact]
    public async Task Many_are_set_aside_and_each_is_named_once()
    {
        for (var number = 0; number < 40; number++)
        {
            await Store($"e{number}");
            Assert.NotNull(await Claim(Short, most: 1));
        }

        await Task.Delay(Short * 2);
        var named = (await Task.WhenAll(Enumerable.Range(0, 6).Select(_ => Task.Run(() => SetAside(most: 1))))).SelectMany(names => names).ToList();
        Assert.Equal(40, named.Count);
        Assert.Equal(40, named.Distinct().Count());
    }

    // Finding work.

    [Fact]
    public async Task Work_is_found_without_reading_all_that_is_done()
    {
        await database.RunAsync("insert into inbox (feed, event_id, shop, body, state) select 'north-web', 'done-' || number, 'north', '', 'done' from generate_series(1, 30000) number");
        await FewRowsAreRead();
    }

    [Fact]
    public async Task Work_is_found_without_reading_all_that_waits()
    {
        await database.RunAsync("insert into inbox (feed, event_id, shop, body, state) select 'north-web', 'waits-' || number, 'north', '', 'waiting' from generate_series(1, 30000) number");
        await FewRowsAreRead();
    }

    [Fact]
    public async Task Work_is_found_without_reading_all_that_waits_for_later()
    {
        await database.RunAsync("insert into inbox (feed, event_id, shop, body, state, due_at) select 'north-web', 'later-' || number, 'north', '', 'waiting', now() + interval '1 hour' from generate_series(1, 30000) number");
        await FewRowsAreRead();
    }

    async Task FewRowsAreRead()
    {
        await database.RunAsync("analyze inbox");
        for (var number = 0; number < 10; number++)
            await Store($"e{number}");
        var before = await RowsRead();
        for (var number = 0; number < 10; number++)
        {
            Assert.Empty(await SetAside());
            Assert.NotNull(await Claim(onlyWaiting: number % 2 == 0));
        }

        Assert.InRange(await RowsRead() - before, 0, 3000);
    }

    /// <summary>How many rows of the inbox were read so far, through an index or not.</summary>
    async Task<long> RowsRead()
    {
        await using var connection = await _source.OpenConnectionAsync();
        await using (var flush = new NpgsqlCommand("select pg_stat_force_next_flush()", connection))
            await flush.ExecuteNonQueryAsync();
        // The numbers of a connection are added up when it is idle for a moment.
        NpgsqlConnection.ClearPool(connection);
        await Task.Delay(1500);
        return await database.AskAsync<long>("select seq_tup_read + idx_tup_fetch from pg_stat_user_tables where relname = 'inbox'");
    }

    [Fact]
    public async Task The_one_that_has_waited_longest_comes_first()
    {
        foreach (var id in new[] { "c", "a", "b" })
        {
            await Store(id);
            await Task.Delay(5);
        }

        Assert.Equal(["c", "a", "b"], [(await Claim())!.EventId, (await Claim())!.EventId, (await Claim())!.EventId]);
    }

    [Fact]
    public async Task Many_workers_at_once_each_get_another_event()
    {
        const int events = 300;
        for (var number = 0; number < events; number++)
            await Store($"e{number}");
        var claimed = await Task.WhenAll(Enumerable.Range(0, 12).Select(_ => Task.Run(async () =>
        {
            var mine = new List<string>();
            while (await Claim() is { } claim)
                mine.Add(claim.EventId);
            return mine;
        })));
        Assert.Equal(events, claimed.Sum(mine => mine.Count));
        Assert.Equal(events, claimed.SelectMany(mine => mine).Distinct().Count());
    }

    [Fact]
    public async Task Many_workers_at_once_do_not_share_an_event_whose_claim_ended()
    {
        for (var round = 0; round < 20; round++)
        {
            await database.RunAsync("truncate inbox, conflicts");
            await Store();
            Assert.NotNull(await Claim(Short));
            await Task.Delay(Short * 2);
            var claims = await Task.WhenAll(Enumerable.Range(0, 8).Select(_ => Task.Run(() => Claim())));
            Assert.Single(claims, claim => claim is not null);
        }
    }

    [Fact]
    public async Task Nobody_waits_for_a_row_that_somebody_holds()
    {
        await Store("held");
        await Store("worn-out");
        await Store("free");
        Assert.Equal("held", (await Claim(Short))!.EventId);
        Assert.Equal("worn-out", (await Claim(Short, most: 1))!.EventId);
        await Task.Delay(Short * 2);
        await using var connection = await database.Outside.OpenConnectionAsync();
        await using var transaction = await connection.BeginTransactionAsync();
        await using (var hold = new NpgsqlCommand("select 1 from inbox where event_id <> 'free' for update", connection, transaction))
            await hold.ExecuteNonQueryAsync();

        Assert.Empty(await SetAside(most: 1).WaitAsync(TimeSpan.FromSeconds(1)));
        var claim = await Claim(most: 1).WaitAsync(TimeSpan.FromSeconds(1));
        Assert.Equal("free", claim!.EventId);
        Assert.Equal(Stored.New, await Store("another").WaitAsync(TimeSpan.FromSeconds(1)));
    }

    // Finishing.

    [Fact]
    public async Task Only_the_token_that_holds_an_event_can_finish_it()
    {
        await Store();
        var claim = (await Claim())!;
        Assert.False(await Finish(claim with { Token = Guid.NewGuid() }));
        Assert.StartsWith("working 1", await Row(), StringComparison.Ordinal);
        Assert.True(await Finish(claim));
        Assert.Equal("done 1 1 -", await Row());
        Assert.False(await Finish(claim));
    }

    [Fact]
    public async Task Whoever_lost_the_claim_cannot_finish()
    {
        await Store();
        var lost = (await Claim(Short))!;
        await Task.Delay(Short * 2);
        var held = (await Claim())!;
        Assert.False(await Finish(lost));
        Assert.True(await Finish(held));
        Assert.False(await Finish(lost));
        Assert.Equal("done 2 1 -", await Row());
    }

    [Fact]
    public async Task Whoever_is_slow_but_was_not_replaced_can_finish()
    {
        await Store();
        var slow = (await Claim(Short))!;
        await Task.Delay(Short * 2);
        Assert.True(await Finish(slow));
    }

    [Fact]
    public async Task A_finish_counts_only_with_its_transaction()
    {
        await Store();
        var claim = (await Claim())!;
        Assert.True(await Finish(claim, commit: false));
        Assert.StartsWith("working 1", await Row(), StringComparison.Ordinal);
        Assert.True(await Finish(claim));
    }

    [Fact]
    public async Task A_finish_is_for_its_own_event_only()
    {
        await Store("a");
        await Store("b");
        var first = (await Claim())!;
        var second = (await Claim())!;
        Assert.False(await Finish(first with { EventId = second.EventId }));
        Assert.True(await Finish(first));
        Assert.StartsWith("working 1", await Row(second.EventId), StringComparison.Ordinal);
    }

    [Fact]
    public async Task While_one_finishes_nobody_else_can_claim()
    {
        await Store();
        var claim = (await Claim(Short))!;
        await using var connection = await _source.OpenConnectionAsync();
        await using var transaction = await connection.BeginTransactionAsync();
        Assert.True(await _inbox.FinishAsync(claim, connection, transaction));
        await Task.Delay(Short * 2);
        Assert.Null(await Claim().WaitAsync(TimeSpan.FromSeconds(1)));
        await transaction.CommitAsync();
        Assert.Null(await Claim());
    }

    // Giving back.

    [Fact]
    public async Task What_is_given_back_waits_first()
    {
        await Store();
        Assert.Equal("waiting", await GiveBack((await Claim())!, TimeSpan.FromMilliseconds(400)));
        Assert.Equal("waiting 1 1 a reason", await Row());
        Assert.Null(await Claim());
        await Task.Delay(500);
        Assert.Equal(2, (await Claim())!.Attempt);
    }

    [Fact]
    public async Task With_its_attempts_used_up_it_is_set_aside()
    {
        await Store();
        Assert.Equal("waiting", await GiveBack((await Claim())!, most: 2));
        Assert.Equal("aside", await GiveBack((await Claim())!, most: 2));
        Assert.Equal("aside 2 1 a reason", await Row());
        Assert.Null(await Claim(most: 99));
    }

    [Fact]
    public async Task What_does_not_count_can_go_on_for_ever()
    {
        await Store();
        for (var time = 0; time < 8; time++)
        {
            var claim = (await Claim(most: 2))!;
            Assert.Equal(1, claim.Attempt);
            Assert.Equal("waiting", await GiveBack(claim, counts: false, most: 2));
            Assert.Empty(await SetAside(most: 2));
        }

        Assert.Equal("waiting 0 1 a reason", await Row());
        // What counted before still counts.
        Assert.Equal("waiting", await GiveBack((await Claim(most: 2))!, most: 2));
        Assert.Equal("waiting", await GiveBack((await Claim(most: 2))!, counts: false, most: 2));
        Assert.Equal("waiting 1 1 a reason", await Row());
        Assert.Equal("aside", await GiveBack((await Claim(most: 2))!, most: 2));
    }

    [Fact]
    public async Task For_good_means_aside_at_once()
    {
        await Store();
        Assert.Equal("aside", await GiveBack((await Claim())!, forGood: true));
        Assert.Equal("aside 1 1 a reason", await Row());
        Assert.Null(await Claim());
    }

    [Fact]
    public async Task Only_the_token_that_holds_an_event_can_give_it_back()
    {
        await Store();
        var lost = (await Claim(Short))!;
        Assert.Null(await GiveBack(lost with { Token = Guid.NewGuid() }));
        await Task.Delay(Short * 2);
        var held = (await Claim())!;
        Assert.Null(await GiveBack(lost, forGood: true));
        Assert.StartsWith("working 2", await Row(), StringComparison.Ordinal);
        Assert.True(await Finish(held));
        Assert.Null(await GiveBack(held, forGood: true));
        Assert.StartsWith("done 2", await Row(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task An_event_that_is_done_shows_no_old_reason()
    {
        await Store();
        await GiveBack((await Claim())!);
        Assert.True(await Finish((await Claim())!));
        Assert.Equal("done 2 1 -", await Row());
    }

    [Fact]
    public async Task What_is_set_aside_is_still_the_same_event_when_it_comes_again()
    {
        await Store();
        await GiveBack((await Claim())!, forGood: true);
        Assert.Equal(Stored.Already, await Store());
        Assert.Equal("aside 1 2 a reason", await Row());
    }

    static string StoreFile() => Path.Combine(
        Environment.GetEnvironmentVariable("PLATFORM_SQL") ?? Path.Combine(Root(), "db"), "store.sql");

    static string Root([System.Runtime.CompilerServices.CallerFilePath] string file = "") => Path.GetFullPath(Path.Combine(Path.GetDirectoryName(file)!, "..", ".."));
}
