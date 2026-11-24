using System.Security.Cryptography;
using System.Text;
using Npgsql;

namespace Platform.Data;

/// <summary>Makes the tables. A file that was applied once may never change.</summary>
public static class Migrator
{
    public static async Task ApplyAsync(NpgsqlDataSource source, CancellationToken stop)
    {
        await using var connection = await source.OpenConnectionAsync(stop);
        await using var transaction = await connection.BeginTransactionAsync(stop);
        await Run("select pg_advisory_xact_lock(4711)");
        await Run("create table if not exists schema_files (name text primary key, hash text not null, applied_at timestamptz not null default now())");
        foreach (var name in Sql.Migrations())
        {
            var text = Sql.Read(name);
            var hash = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(text)));
            var known = await Ask("select hash from schema_files where name = $1", name);
            if (known is null)
            {
                await Run(text);
                await Run("insert into schema_files (name, hash) values ($1, $2)", name, hash);
            }
            else if (known != hash)
            {
                throw new InvalidOperationException($"db/{name} was changed after it was applied. Add a new file instead.");
            }
        }

        await transaction.CommitAsync(stop);

        async Task Run(string text, params string[] values)
        {
            await using var command = Command(text, values);
            await command.ExecuteNonQueryAsync(stop);
        }

        async Task<string?> Ask(string text, params string[] values)
        {
            await using var command = Command(text, values);
            return (string?)await command.ExecuteScalarAsync(stop);
        }

        NpgsqlCommand Command(string text, string[] values)
        {
            var command = new NpgsqlCommand(text, connection, transaction);
            foreach (var value in values)
                command.Parameters.Add(new NpgsqlParameter<string> { TypedValue = value });
            return command;
        }
    }
}
