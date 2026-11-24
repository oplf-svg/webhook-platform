using Npgsql;
using NpgsqlTypes;

namespace Platform.Data;

public enum Stored
{
    /// <summary>It was new, and now it is kept.</summary>
    New,

    /// <summary>The same event was already there.</summary>
    Already,

    /// <summary>Another body was already there under this id.</summary>
    Conflict,
}

/// <summary>An event that is with one worker. The token says with which.</summary>
public sealed record Claim(string Feed, string EventId, string Shop, byte[] Body, int Attempt, Guid Token);

/// <summary>An event that was set aside because its attempts are used up.</summary>
public sealed record Aside(string Feed, string EventId, int Attempts, string Reason);

/// <summary>The five statements of db/, each called in one place. What they have to hold stands in docs/contract.md.</summary>
public sealed class Inbox(NpgsqlDataSource source)
{
    readonly Lazy<string> _store = new(() => Sql.Read("store.sql"));
    readonly Lazy<string> _setAside = new(() => Sql.Read("set_aside.sql"));
    readonly Lazy<string> _claim = new(() => Sql.Read("claim.sql"));
    readonly Lazy<string> _finish = new(() => Sql.Read("finish.sql"));
    readonly Lazy<string> _giveBack = new(() => Sql.Read("give_back.sql"));

    /// <summary>Reads what storing needs, so that a missing statement stops the start and not the first event.</summary>
    public void ReadyToStore() => _ = _store.Value;

    /// <summary>The same for what a worker needs.</summary>
    public void ReadyToWork() => _ = (_setAside.Value, _claim.Value, _finish.Value, _giveBack.Value);

    /// <summary>Asks for the table itself, because a database without it answers too.</summary>
    public async Task AskAsync()
    {
        await using var command = source.CreateCommand("select exists (select from inbox)");
        await command.ExecuteScalarAsync();
    }

    public async Task<Stored> StoreAsync(string feed, string eventId, string shop, byte[] body)
    {
        await using var command = source.CreateCommand(_store.Value);
        Text(command, "feed", feed);
        Text(command, "event_id", eventId);
        Text(command, "shop", shop);
        Add(command, "body", NpgsqlDbType.Bytea, body);
        await using var reader = await command.ExecuteReaderAsync();
        var answer = await reader.ReadAsync() ? reader.GetString(0) : null;
        if (await reader.ReadAsync())
            throw new InvalidOperationException("store.sql returned more than one row.");
        return answer switch
        {
            "stored" => Stored.New,
            "already" => Stored.Already,
            "conflict" => Stored.Conflict,
            // Never guess: whoever sent the event has to send it again.
            _ => throw new InvalidOperationException("store.sql did not say what it did."),
        };
    }

    /// <summary>Sets aside what is due and has no attempt left, and returns it, so that somebody can say so.</summary>
    public async Task<List<Aside>> SetAsideAsync(int maxAttempts)
    {
        await using var command = source.CreateCommand(_setAside.Value);
        Add(command, "max_attempts", NpgsqlDbType.Integer, maxAttempts);
        await using var reader = await command.ExecuteReaderAsync();
        var aside = new List<Aside>();
        while (await reader.ReadAsync())
            aside.Add(new Aside(reader.GetString(0), reader.GetString(1), reader.GetInt32(2), reader.GetString(3)));
        return aside;
    }

    /// <summary>Claims an event. With <paramref name="onlyWaiting"/>, not one whose last claim ended without a word.</summary>
    public async Task<Claim?> ClaimAsync(TimeSpan lease, int maxAttempts, bool onlyWaiting)
    {
        var token = Guid.NewGuid();
        await using var command = source.CreateCommand(_claim.Value);
        Add(command, "token", NpgsqlDbType.Uuid, token);
        Add(command, "lease", NpgsqlDbType.Interval, lease);
        Add(command, "max_attempts", NpgsqlDbType.Integer, maxAttempts);
        Add(command, "only_waiting", NpgsqlDbType.Boolean, onlyWaiting);
        await using var reader = await command.ExecuteReaderAsync();
        if (!await reader.ReadAsync())
            return null;
        var claim = new Claim(reader.GetString(0), reader.GetString(1), reader.GetString(2), reader.GetFieldValue<byte[]>(3), reader.GetInt32(4), token);
        return await reader.ReadAsync() ? throw new InvalidOperationException("claim.sql returned more than one row.") : claim;
    }

    /// <summary>Marks the event as done, if the claim still holds. It counts only when the transaction commits.</summary>
    public async Task<bool> FinishAsync(Claim claim, NpgsqlConnection connection, NpgsqlTransaction transaction)
    {
        await using var command = new NpgsqlCommand(_finish.Value, connection, transaction);
        Text(command, "feed", claim.Feed);
        Text(command, "event_id", claim.EventId);
        Add(command, "token", NpgsqlDbType.Uuid, claim.Token);
        return await command.ExecuteNonQueryAsync() switch
        {
            1 => true,
            0 => false,
            _ => throw new InvalidOperationException("finish.sql did not change one row or none."),
        };
    }

    /// <summary>
    /// Gives the event back, if the claim still holds. Returns its new state, or null if it was not ours. An attempt that does
    /// not count was not the fault of the event.
    /// </summary>
    public async Task<string?> GiveBackAsync(Claim claim, string reason, TimeSpan delay, bool forGood, bool counts, int maxAttempts)
    {
        await using var command = source.CreateCommand(_giveBack.Value);
        Text(command, "feed", claim.Feed);
        Text(command, "event_id", claim.EventId);
        Add(command, "token", NpgsqlDbType.Uuid, claim.Token);
        Text(command, "reason", reason);
        Add(command, "delay", NpgsqlDbType.Interval, delay);
        Add(command, "for_good", NpgsqlDbType.Boolean, forGood);
        Add(command, "counts", NpgsqlDbType.Boolean, counts);
        Add(command, "max_attempts", NpgsqlDbType.Integer, maxAttempts);
        return (string?)await command.ExecuteScalarAsync();
    }

    static void Text(NpgsqlCommand command, string name, string value) => Add(command, name, NpgsqlDbType.Text, value);

    static void Add(NpgsqlCommand command, string name, NpgsqlDbType type, object value) =>
        command.Parameters.Add(new NpgsqlParameter(name, type) { Value = value });
}
