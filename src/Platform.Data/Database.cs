using Npgsql;

namespace Platform.Data;

public static class Database
{
    // A process can stop at any moment, also while it holds rows. These make sure that the database lets go of them.
    const string Limits = "-c statement_timeout=5s -c lock_timeout=2s -c idle_in_transaction_session_timeout=5s -c transaction_timeout=10s";

    public static NpgsqlDataSource Open(string connection, string name)
    {
        var builder = new NpgsqlDataSourceBuilder(connection);
        builder.ConnectionStringBuilder.ApplicationName = name;
        builder.ConnectionStringBuilder.Options = Limits;
        builder.ConnectionStringBuilder.Timeout = 3;
        builder.ConnectionStringBuilder.CommandTimeout = 8;
        // What takes too long is cut off and not cancelled. A commit that waits for a standby and is cancelled would be
        // answered with a yes, although the standby does not have it.
        builder.ConnectionStringBuilder.CancellationTimeout = -1;
        return builder.Build();
    }
}
