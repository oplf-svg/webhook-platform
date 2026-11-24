using Npgsql;
using Platform.Data;
using Xunit;

namespace Platform.Tests;

/// <summary>A fresh database for one group of tests, made the same way as everywhere else.</summary>
public sealed class TestDatabase : IAsyncLifetime
{
    readonly string _name = $"test_{Guid.NewGuid():N}";

    public string Connection { get; private set; } = "";

    /// <summary>For looking at the tables from outside, without the code that is being tested.</summary>
    public NpgsqlDataSource Outside { get; private set; } = null!;

    static string Admin => Environment.GetEnvironmentVariable("PLATFORM_TEST_DATABASE") is { Length: > 0 } value
        ? value
        : throw new InvalidOperationException("PLATFORM_TEST_DATABASE is not set. Start the tests with .github/scripts/tests.sh.");

    public async ValueTask InitializeAsync()
    {
        await AdminAsync($"create database {_name}");
        Connection = new NpgsqlConnectionStringBuilder(Admin) { Database = _name }.ConnectionString;
        Outside = NpgsqlDataSource.Create(Connection);
        await using var source = Database.Open(Connection, "migrate");
        await Migrator.ApplyAsync(source, CancellationToken.None);
    }

    public async ValueTask DisposeAsync()
    {
        if (Outside is not null)
            await Outside.DisposeAsync();
        NpgsqlConnection.ClearAllPools();
        await AdminAsync($"drop database if exists {_name} with (force)");
    }

    public async Task RunAsync(string sql, params object[] values)
    {
        await using var command = Command(sql, values);
        await command.ExecuteNonQueryAsync();
    }

    public async Task<T> AskAsync<T>(string sql, params object[] values)
    {
        await using var command = Command(sql, values);
        return (T)(await command.ExecuteScalarAsync())!;
    }

    public async Task<List<T>> ListAsync<T>(string sql, params object[] values)
    {
        await using var command = Command(sql, values);
        await using var reader = await command.ExecuteReaderAsync();
        var rows = new List<T>();
        while (await reader.ReadAsync())
            rows.Add(reader.GetFieldValue<T>(0));
        return rows;
    }

    NpgsqlCommand Command(string sql, object[] values)
    {
        var command = Outside.CreateCommand(sql);
        foreach (var value in values)
            command.Parameters.Add(new NpgsqlParameter { Value = value });
        return command;
    }

    static async Task AdminAsync(string sql)
    {
        await using var connection = new NpgsqlConnection(Admin);
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(sql, connection);
        await command.ExecuteNonQueryAsync();
    }
}
