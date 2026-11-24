using System.Globalization;
using Microsoft.Extensions.Configuration;

namespace Platform.Worker;

public sealed record WorkerSettings
{
    /// <summary>How many events one process works on at the same moment.</summary>
    public int Loops { get; init; } = 4;

    /// <summary>
    /// How long an event stays with a worker that says nothing more. Longer than the 10 seconds after which the database ends
    /// any transaction, so a worker that still runs does not lose its claim.
    /// </summary>
    public TimeSpan Lease { get; init; } = TimeSpan.FromSeconds(30);

    /// <summary>How long to wait when there is nothing to do.</summary>
    public TimeSpan Pause { get; init; } = TimeSpan.FromMilliseconds(250);

    public int MaxAttempts { get; init; } = 5;

    /// <summary>How long a failed event waits the first time. It doubles with each attempt.</summary>
    public TimeSpan FirstDelay { get; init; } = TimeSpan.FromSeconds(1);

    /// <summary>How long an event waits at least when the database was in trouble. That is not the fault of the event.</summary>
    public TimeSpan TroubleDelay { get; init; } = TimeSpan.FromSeconds(5);

    /// <summary>Up to twice as long, and never the same, so that not all of them come back at the same moment.</summary>
    public TimeSpan DelayAfterTrouble() => TroubleDelay * (1 + Random.Shared.NextDouble());

    public TimeSpan DelayAfter(int attempt) => FirstDelay * Math.Pow(2, Math.Clamp(attempt, 1, 12) - 1);

    public static WorkerSettings Read(IConfiguration configuration)
    {
        var normal = new WorkerSettings();
        return new WorkerSettings
        {
            Loops = (int)Number("PLATFORM_LOOPS", normal.Loops),
            Lease = TimeSpan.FromMilliseconds(Number("PLATFORM_LEASE_MS", normal.Lease.TotalMilliseconds)),
            Pause = TimeSpan.FromMilliseconds(Number("PLATFORM_PAUSE_MS", normal.Pause.TotalMilliseconds)),
            MaxAttempts = (int)Number("PLATFORM_MAX_ATTEMPTS", normal.MaxAttempts),
            FirstDelay = TimeSpan.FromMilliseconds(Number("PLATFORM_FIRST_DELAY_MS", normal.FirstDelay.TotalMilliseconds)),
            TroubleDelay = TimeSpan.FromMilliseconds(Number("PLATFORM_TROUBLE_DELAY_MS", normal.TroubleDelay.TotalMilliseconds)),
        };

        double Number(string name, double otherwise) => configuration[name] switch
        {
            null or "" => otherwise,
            var text when int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out var number) && number > 0 => number,
            _ => throw new InvalidOperationException($"{name} is not a whole number above 0."),
        };
    }
}
