using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;
using Platform.Core;
using Platform.Data;

namespace Platform.Worker;

/// <summary>Takes events from the inbox and makes their effect, each exactly once.</summary>
public sealed partial class EventWorker(NpgsqlDataSource source, Inbox inbox, Ledger ledger, WorkerSettings settings, ILogger<EventWorker> log) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var stop = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        // Only the first loop takes events whose last claim ended without a word, so it takes them one at a time. One of
        // them may be what killed the worker, and the others were only there. They must not go down with it again.
        await Task.WhenAll(Enumerable.Range(0, settings.Loops).Select(number => LoopAsync(number == 0, stop)));
    }

    async Task LoopAsync(bool careful, CancellationTokenSource stop)
    {
        try
        {
            // Asked to stop, a loop finishes the event it has and takes no other one.
            while (!stop.IsCancellationRequested)
            {
                if (!await TryWorkOnceAsync(careful))
                    await Task.Delay(settings.Pause, stop.Token).ContinueWith(_ => { }, TaskScheduler.Default);
            }
        }
        finally
        {
            // A loop that breaks takes the others with it. The process ends and says so, instead of going on with less.
            await stop.CancelAsync();
        }
    }

    async Task<bool> TryWorkOnceAsync(bool careful)
    {
        try
        {
            return await WorkOnceAsync(careful);
        }
        catch (Exception problem) when (Trouble.Is(problem))
        {
            // Nothing is lost: what was claimed comes back by itself when its time is over.
            NoDatabase(log, problem);
            return false;
        }
    }

    /// <summary>Works on one event. Returns false if there was none.</summary>
    public async Task<bool> WorkOnceAsync(bool careful)
    {
        if (careful)
        {
            foreach (var aside in await inbox.SetAsideAsync(settings.MaxAttempts))
                SetAside(log, aside.Feed, aside.EventId, aside.Attempts, aside.Reason);
        }

        var claim = await inbox.ClaimAsync(settings.Lease, settings.MaxAttempts, onlyWaiting: !careful);
        if (claim is null)
            return false;

        Fault.At("worker.after_claim");
        try
        {
            if (await HandleAsync(claim))
                Done(log, claim.Feed, claim.EventId, claim.Attempt);
            else
                Lost(log, claim.Feed, claim.EventId, claim.Attempt);
        }
        catch (UnreadableException problem)
        {
            await GiveBackAsync(claim, problem.Message, TimeSpan.Zero, forGood: true);
        }
        catch (PostgresException problem) when (problem is { SqlState: PostgresErrorCodes.UniqueViolation, ConstraintName: Ledger.SafetyNet })
        {
            await GiveBackAsync(claim, "the order is already there", TimeSpan.Zero, forGood: true);
        }
        catch (Exception problem) when (Trouble.Is(problem))
        {
            Failed(log, claim.Feed, claim.EventId, claim.Attempt, problem);
            await GiveBackAsync(claim, $"trouble with the database{Code(problem)}", settings.DelayAfterTrouble(), forGood: false, counts: false);
        }
        catch (Exception problem)
        {
            Failed(log, claim.Feed, claim.EventId, claim.Attempt, problem);
            await GiveBackAsync(claim, $"a step failed{Code(problem)}", settings.DelayAfter(claim.Attempt), forGood: false);
        }

        return true;
    }

    /// <summary>The mark and the effect, in one transaction. Returns false if the claim was lost, and then nothing happened.</summary>
    async Task<bool> HandleAsync(Claim claim)
    {
        var order = Order.Read(claim.Body);
        Fault.At($"worker.read.{order.Sku}");
        await using var connection = await source.OpenConnectionAsync();
        await using var transaction = await connection.BeginTransactionAsync();
        // The mark comes first. It fails for whoever lost the claim, and it keeps the row until the end of the transaction.
        if (!await inbox.FinishAsync(claim, connection, transaction))
            return false;
        Fault.At("worker.after_mark");
        await ledger.ApplyAsync(claim, order, connection, transaction);
        Fault.At("worker.after_effect");
        await transaction.CommitAsync();
        Fault.At("worker.after_commit");
        return true;
    }

    async Task GiveBackAsync(Claim claim, string reason, TimeSpan delay, bool forGood, bool counts = true)
    {
        var state = await inbox.GiveBackAsync(claim, reason, delay, forGood, counts, settings.MaxAttempts);
        GivenBack(log, state == "aside" ? LogLevel.Error : LogLevel.Warning, claim.Feed, claim.EventId, claim.Attempt, state ?? "no longer ours", reason);
    }

    static string Code(Exception problem) => problem is PostgresException { SqlState: var code } ? $": {code}" : "";

    [LoggerMessage(LogLevel.Information, "{Feed} {Event}: done at attempt {Attempt}")]
    static partial void Done(ILogger log, string feed, string @event, int attempt);

    [LoggerMessage(LogLevel.Warning, "{Feed} {Event}: attempt {Attempt} lost its claim and changed nothing")]
    static partial void Lost(ILogger log, string feed, string @event, int attempt);

    [LoggerMessage("{Feed} {Event}: attempt {Attempt} ended as {State}: {Reason}")]
    static partial void GivenBack(ILogger log, LogLevel level, string feed, string @event, int attempt, string state, string reason);

    [LoggerMessage(LogLevel.Error, "{Feed} {Event}: set aside after attempt {Attempt}: {Reason}")]
    static partial void SetAside(ILogger log, string feed, string @event, int attempt, string reason);

    [LoggerMessage(LogLevel.Error, "{Feed} {Event}: attempt {Attempt} failed")]
    static partial void Failed(ILogger log, string feed, string @event, int attempt, Exception problem);

    [LoggerMessage(LogLevel.Error, "the database did not answer")]
    static partial void NoDatabase(ILogger log, Exception problem);
}
