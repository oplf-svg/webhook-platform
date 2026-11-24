using System.Globalization;
using Microsoft.Extensions.Logging;
using Platform.Api;
using Platform.Core;
using Xunit;

namespace Platform.Tests;

public sealed class RefusalsTests : IDisposable
{
    sealed class Clock : TimeProvider, ITimer
    {
        TimerCallback? _callback;

        public TimeSpan Now { get; set; }

        public override long TimestampFrequency => TimeSpan.TicksPerSecond;

        public override long GetTimestamp() => Now.Ticks;

        public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
        {
            _callback = callback;
            return this;
        }

        public void Ring() => _callback!(null);

        public bool Change(TimeSpan dueTime, TimeSpan period) => true;

        public void Dispose() => _callback = null;

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }

    sealed class Lines : ILogger<Refusals>
    {
        public List<string> All { get; } = [];

        public IDisposable? BeginScope<TState>(TState state)
            where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter) =>
            All.Add($"{logLevel} {formatter(state, exception)}");
    }

    readonly Clock _clock = new();
    readonly Lines _lines = new();
    readonly Refusals _refusals;

    public RefusalsTests() => _refusals = new Refusals(new Feeds([new Feed("north-web", "north", [Sender.NewSecret()]), new Feed("south-web", "south", [Sender.NewSecret()])]), _clock, _lines);

    public void Dispose()
    {
        _refusals.Dispose();
        _clock.Dispose();
    }

    IEnumerable<int> Numbers() => _lines.All.Select(line => int.Parse(line.Split(' ')[3], CultureInfo.InvariantCulture));

    [Fact]
    public void The_first_one_is_said_at_once()
    {
        _refusals.Add("north-web", "wrong signature");
        Assert.Equal(["Warning north-web: refused 1 since the last line like this, wrong signature"], _lines.All);
    }

    [Fact]
    public void After_that_it_is_one_line_a_minute()
    {
        for (var second = 0; second < 180; second++)
        {
            _clock.Now = TimeSpan.FromSeconds(second);
            for (var each = 0; each < 10; each++)
                _refusals.Add("north-web", "wrong signature");
        }

        Assert.Equal([1, 600, 600], Numbers());
    }

    [Fact]
    public void The_last_ones_are_said_even_if_no_more_come()
    {
        for (var each = 0; each < 10; each++)
            _refusals.Add("north-web", "wrong signature");
        _clock.Now = TimeSpan.FromSeconds(59);
        _clock.Ring();
        Assert.Equal([1], Numbers());
        _clock.Now = TimeSpan.FromSeconds(60);
        _clock.Ring();
        _clock.Ring();
        Assert.Equal([1, 9], Numbers());
        _clock.Now = TimeSpan.FromSeconds(300);
        _clock.Ring();
        Assert.Equal([1, 9], Numbers());
    }

    [Fact]
    public void Feeds_and_reasons_are_counted_apart()
    {
        _refusals.Add("north-web", "wrong signature");
        _refusals.Add("north-web", "wrong time");
        _refusals.Add("south-web", "wrong time");
        Assert.Equal(3, _lines.All.Count);
    }

    [Fact]
    public void A_name_that_somebody_made_up_is_not_repeated_and_makes_one_line()
    {
        for (var number = 0; number < 1000; number++)
            _refusals.Add($"made-up-{number}", "wrong signature");
        Assert.Equal(["Warning no such feed: refused 1 since the last line like this, wrong signature"], _lines.All);
    }
}
