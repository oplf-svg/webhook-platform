using System.Text.RegularExpressions;

namespace Platform.Data;

/// <summary>The SQL of db/, which is built into this assembly.</summary>
static partial class Sql
{
    public static string Read(string name)
    {
        var text = Load(name);
        if (Comment().Replace(text, "").Trim().Length == 0)
            throw new InvalidOperationException($"db/{name} is not written yet.");
        return text;
    }

    /// <summary>The files that make the tables, in their order.</summary>
    public static IReadOnlyList<string> Migrations() =>
        [.. typeof(Sql).Assembly.GetManifestResourceNames().Where(name => Migration().IsMatch(name)).Order(StringComparer.Ordinal)];

    static string Load(string name)
    {
#if FAULTS
        // Only in test builds: another folder, so that the tests can be tried on SQL that is wrong on purpose.
        if (Environment.GetEnvironmentVariable("PLATFORM_SQL") is { Length: > 0 } folder)
            return File.ReadAllText(Path.Combine(folder, name));
#endif
        using var stream = typeof(Sql).Assembly.GetManifestResourceStream(name) ?? throw new InvalidOperationException($"db/{name} is missing.");
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    [GeneratedRegex("--.*$", RegexOptions.Multiline)]
    private static partial Regex Comment();

    [GeneratedRegex(@"^\d{3}_.*\.sql$")]
    private static partial Regex Migration();
}
