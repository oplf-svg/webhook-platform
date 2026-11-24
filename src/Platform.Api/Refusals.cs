using System.Collections.Concurrent;
using Platform.Core;

namespace Platform.Api;

/// <summary>
/// Says what was refused, so that a feed with a wrong secret or a wrong clock does not go unnoticed. At most one line a
/// minute for each feed and reason, because anybody can make us refuse something.
/// </summary>
public sealed partial class Refusals : IDisposable
{
    static readonly TimeSpan Pause = TimeSpan.FromMinutes(1);

    readonly ConcurrentDictionary<(string Feed, string Reason), Count> _counts = new();
    readonly Feeds _feeds;
    readonly TimeProvider _clock;
    readonly ILogger<Refusals> _log;
    readonly ITimer _timer;

    public Refusals(Feeds feeds, TimeProvider clock, ILogger<Refusals> log)
    {
        (_feeds, _clock, _log) = (feeds, clock, log);
        // Without it, the last ones would only be said when there are more.
        _timer = clock.CreateTimer(_ => SayTheRest(), null, Pause / 6, Pause / 6);
    }

    public void Add(string feed, string reason)
    {
        // A name that does not exist is not repeated: anybody can make one up.
        var name = _feeds.Find(feed).Name is { Length: > 0 } known ? known : "no such feed";
        var count = _counts.GetOrAdd((name, reason), _ => new Count());
        count.Add();
        Say(name, reason, count);
    }

    public void Dispose() => _timer.Dispose();

    void SayTheRest()
    {
        foreach (var ((feed, reason), count) in _counts)
            Say(feed, reason, count);
    }

    void Say(string feed, string reason, Count count)
    {
        if (count.TakeAll(_clock) is { } number)
            Refused(_log, feed, number, reason);
    }

    [LoggerMessage(LogLevel.Warning, "{Feed}: refused {Number} since the last line like this, {Reason}")]
    static partial void Refused(ILogger log, string feed, long number, string reason);

    sealed class Count
    {
        readonly Lock _lock = new();
        long _number;
        long? _last;

        public void Add()
        {
            lock (_lock)
                _number++;
        }

        /// <summary>Returns how many there were, if there were any and it is time to say so.</summary>
        public long? TakeAll(TimeProvider clock)
        {
            lock (_lock)
            {
                if (_number == 0 || (_last is { } last && clock.GetElapsedTime(last) < Pause))
                    return null;
                var number = _number;
                (_number, _last) = (0, clock.GetTimestamp());
                return number;
            }
        }
    }
}
