using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using Platform.Data;
using Platform.Worker;
using Xunit;

namespace Platform.Tests;

/// <summary>One worker, one event at a time. What happens when it is killed or stopped is in crash/.</summary>
public sealed class WorkerTests(TestDatabase database) : IClassFixture<TestDatabase>, IAsyncLifetime
{
    readonly NpgsqlDataSource _source = Database.Open(database.Connection, "test");
    readonly WorkerSettings _settings = new() { FirstDelay = TimeSpan.FromMilliseconds(50), TroubleDelay = TimeSpan.FromMilliseconds(50), MaxAttempts = 3 };
    Inbox _inbox = null!;
    EventWorker _worker = null!;

    public async ValueTask InitializeAsync()
    {
        _inbox = new Inbox(_source);
        _worker = new EventWorker(_source, _inbox, new Ledger(), _settings, NullLogger<EventWorker>.Instance);
        await database.RunAsync("truncate inbox, conflicts, orders, totals");
    }

    public async ValueTask DisposeAsync()
    {
        _worker.Dispose();
        await _source.DisposeAsync();
    }

    Task<Stored> Store(string id, byte[] body, string shop = "north", string channel = "web") => _inbox.StoreAsync($"{shop}-{channel}", id, shop, body);

    Task<string> Row(string id) => database.AskAsync<string>("select concat_ws(' ', state, attempts, coalesce(reason, '-')) from inbox where event_id = $1", id);

    Task<string> Total(string shop = "north") => database.AskAsync<string>("select coalesce((select orders || ' ' || cents from totals where shop = $1), 'none')", shop);

    [Fact]
    public async Task With_nothing_to_do_it_says_so() => Assert.False(await _worker.WorkOnceAsync(careful: true));

    [Fact]
    public async Task An_event_becomes_one_order_and_counts_once()
    {
        await Store("e1", Sender.Order("N-1", quantity: 3, cents: 700));
        Assert.True(await _worker.WorkOnceAsync(careful: true));
        Assert.False(await _worker.WorkOnceAsync(careful: true));
        Assert.Equal("done 1 -", await Row("e1"));
        Assert.Equal("1 700", await Total());
        Assert.Equal("north N-1 north-web e1 BOX-S 3 700", await database.AskAsync<string>("select concat_ws(' ', shop, number, feed, event_id, sku, quantity, cents) from orders"));
    }

    [Fact]
    public async Task The_shop_is_the_one_of_the_feed()
    {
        await Store("e1", """{"type":"order.placed","shop":"south","order":"N-1","sku":"BOX-S","quantity":1,"cents":5,"placed_at":"2026-11-24T10:00:00Z"}"""u8.ToArray());
        Assert.True(await _worker.WorkOnceAsync(careful: true));
        Assert.Equal("1 5", await Total("north"));
        Assert.Equal("none", await Total("south"));
    }

    [Fact]
    public async Task What_cannot_be_understood_is_set_aside_at_once_and_nothing_else_waits()
    {
        await Store("bad", "not an order"u8.ToArray());
        await Store("good", Sender.Order("N-1"));
        Assert.True(await _worker.WorkOnceAsync(careful: true));
        Assert.True(await _worker.WorkOnceAsync(careful: true));
        Assert.Equal("aside 1 not JSON, or a name in it stands twice", await Row("bad"));
        Assert.Equal("done 1 -", await Row("good"));
        Assert.Equal("1 1250", await Total());
    }

    [Fact]
    public async Task The_safety_net_keeps_an_order_from_counting_twice()
    {
        await Store("e1", Sender.Order("N-1"));
        await Store("e2", Sender.Order("N-1", cents: 1));
        await Store("e3", Sender.Order("N-1"), "south");
        for (var i = 0; i < 3; i++)
            Assert.True(await _worker.WorkOnceAsync(careful: true));
        Assert.Equal("done 1 -", await Row("e1"));
        Assert.Equal("aside 1 the order is already there", await Row("e2"));
        // Another shop may use the same number.
        Assert.Equal("done 1 -", await Row("e3"));
        Assert.Equal("1 1250", await Total("north"));
        Assert.Equal("1 1250", await Total("south"));
    }

    [Fact]
    public async Task Two_channels_of_a_shop_can_use_the_same_number()
    {
        await Store("e1", Sender.Order("N-1", cents: 700));
        await Store("e2", Sender.Order("N-1", cents: 50), channel: "app");
        // On one channel it is the same order, whatever the id of the event.
        await Store("e3", Sender.Order("N-1", cents: 9), channel: "app");
        for (var i = 0; i < 3; i++)
            Assert.True(await _worker.WorkOnceAsync(careful: true));
        Assert.Equal("done 1 -", await Row("e1"));
        Assert.Equal("done 1 -", await Row("e2"));
        Assert.Equal("aside 1 the order is already there", await Row("e3"));
        Assert.Equal("2 750", await Total());
    }

    [Fact]
    public async Task Trouble_with_the_database_is_not_the_fault_of_the_event()
    {
        await Store("e0", Sender.Order("N-0"));
        Assert.True(await _worker.WorkOnceAsync(careful: true));
        await Store("e1", Sender.Order("N-1"));
        await using (var connection = await database.Outside.OpenConnectionAsync())
        await using (var transaction = await connection.BeginTransactionAsync())
        {
            await using var hold = new NpgsqlCommand("select 1 from totals for update", connection, transaction);
            await hold.ExecuteNonQueryAsync();
            Assert.True(await _worker.WorkOnceAsync(careful: true));
        }

        Assert.Equal("waiting 0 trouble with the database: 55P03", await Row("e1"));
        Assert.Equal("1 1250", await Total());
        Assert.Equal(0, await database.AskAsync<long>("select count(*) from orders where number = 'N-1'"));
        await Task.Delay(150);
        Assert.True(await _worker.WorkOnceAsync(careful: true));
        Assert.Equal("done 1 -", await Row("e1"));
        Assert.Equal("2 2500", await Total());
    }

    [Fact]
    public async Task A_step_that_always_fails_ends_aside_and_counts_nothing()
    {
        await database.RunAsync("alter table totals add constraint full_up check (orders < 1) not valid");
        try
        {
            await Store("e1", Sender.Order("N-1"));
            for (var attempt = 1; attempt <= 3; attempt++)
            {
                Assert.True(await _worker.WorkOnceAsync(careful: true));
                await Task.Delay(_settings.DelayAfter(attempt) * 1.5);
            }

            Assert.False(await _worker.WorkOnceAsync(careful: true));
            Assert.Equal("aside 3 a step failed: 23514", await Row("e1"));
            Assert.Equal("none", await Total());
            Assert.Equal(0, await database.AskAsync<long>("select count(*) from orders"));
        }
        finally
        {
            await database.RunAsync("alter table totals drop constraint full_up");
        }
    }

    [Fact]
    public void Each_wait_is_longer_than_the_one_before()
    {
        var settings = new WorkerSettings { FirstDelay = TimeSpan.FromSeconds(1) };
        Assert.Equal([1, 2, 4, 8, 16], Enumerable.Range(1, 5).Select(attempt => (int)settings.DelayAfter(attempt).TotalSeconds));
    }

    [Fact]
    public void Events_that_met_trouble_do_not_come_back_together()
    {
        var settings = new WorkerSettings { TroubleDelay = TimeSpan.FromSeconds(10) };
        var delays = Enumerable.Range(0, 200).Select(_ => settings.DelayAfterTrouble().TotalSeconds).ToList();
        Assert.All(delays, delay => Assert.InRange(delay, 10, 20));
        Assert.True(delays.Max() - delays.Min() > 8);
    }

    [Theory]
    [InlineData("57P01", true)]
    [InlineData("57014", true)]
    [InlineData("08006", true)]
    [InlineData("53300", true)]
    [InlineData("40001", true)]
    [InlineData("40P01", true)]
    [InlineData("55P03", true)]
    [InlineData("42P01", false)]
    [InlineData("42501", false)]
    [InlineData("28P01", false)]
    [InlineData("23514", false)]
    [InlineData("25P03", false)]
    [InlineData("22001", false)]
    public void Trouble_goes_away_and_a_mistake_does_not(string code, bool trouble) =>
        Assert.Equal(trouble, Trouble.Is(new PostgresException("", "ERROR", "ERROR", code)));

    [Fact]
    public void No_answer_is_trouble_and_anything_else_is_not()
    {
        Assert.True(Trouble.Is(new NpgsqlException("no connection")));
        Assert.True(Trouble.Is(new TimeoutException()));
        Assert.False(Trouble.Is(new InvalidOperationException()));
    }

    [Theory]
    [InlineData("0")]
    [InlineData("-1")]
    [InlineData("1.5")]
    [InlineData("many")]
    public void A_setting_that_makes_no_sense_stops_the_start(string value) => Assert.Throws<InvalidOperationException>(() =>
        WorkerSettings.Read(new ConfigurationBuilder().AddInMemoryCollection([new("PLATFORM_LOOPS", value)]).Build()));

    [Fact]
    public async Task Many_workers_make_every_effect_once()
    {
        const int events = 400;
        for (var number = 0; number < events; number++)
            await Store($"e{number}", Sender.Order($"N-{number}", cents: number));
        await Task.WhenAll(Enumerable.Range(0, 10).Select(_ => Task.Run(async () =>
        {
            while (await _worker.WorkOnceAsync(careful: true))
            {
            }
        })));
        Assert.Equal($"{events} {events * (events - 1) / 2}", await Total());
        Assert.Equal(events, await database.AskAsync<long>("select count(*) from inbox where state = 'done' and attempts = 1"));
    }
}
