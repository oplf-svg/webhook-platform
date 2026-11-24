using Npgsql;

namespace Platform.Data;

/// <summary>Trouble with the database that is nobody's mistake and goes away: what failed is worth trying again.</summary>
public static class Trouble
{
    public static bool Is(Exception problem) => problem switch
    {
        // No connection, no room, taken back by the server, or somebody else holds what is needed. Everything else that the
        // server says is a mistake: a table that is not there, a right that is missing, a value that cannot be.
        PostgresException { SqlState: var code } => code[..2] is "08" or "53" or "57" or "58" || code is "40001" or "40P01" or "55P03" or "55006",
        NpgsqlException or TimeoutException => true,
        _ => false,
    };
}
