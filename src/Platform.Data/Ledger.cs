using Npgsql;
using NpgsqlTypes;
using Platform.Core;

namespace Platform.Data;

/// <summary>The effect of an event.</summary>
public sealed class Ledger
{
    /// <summary>The unique key that keeps an order from counting twice, whatever the inbox does.</summary>
    public const string SafetyNet = "orders_once";

    readonly string _effect = Sql.Read("effect.sql");

    public async Task ApplyAsync(Claim claim, Order order, NpgsqlConnection connection, NpgsqlTransaction transaction)
    {
        await using var command = new NpgsqlCommand(_effect, connection, transaction);
        Add("shop", NpgsqlDbType.Text, claim.Shop);
        Add("feed", NpgsqlDbType.Text, claim.Feed);
        Add("event_id", NpgsqlDbType.Text, claim.EventId);
        Add("number", NpgsqlDbType.Text, order.Number);
        Add("sku", NpgsqlDbType.Text, order.Sku);
        Add("quantity", NpgsqlDbType.Integer, order.Quantity);
        Add("cents", NpgsqlDbType.Bigint, order.Cents);
        Add("placed_at", NpgsqlDbType.TimestampTz, order.PlacedAt);
        if (await command.ExecuteNonQueryAsync() != 1)
            throw new InvalidOperationException("effect.sql did not change exactly one total.");

        void Add(string name, NpgsqlDbType type, object value) =>
            command.Parameters.Add(new NpgsqlParameter(name, type) { Value = value });
    }
}
