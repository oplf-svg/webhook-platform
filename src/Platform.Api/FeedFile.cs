using System.Text.Json;
using System.Text.Json.Serialization;
using Platform.Core;

namespace Platform.Api;

/// <summary>
/// The feeds and their secrets come from one file, because a file can be closed to everybody else. The settings of a
/// process can be read in too many places.
/// </summary>
static class FeedFile
{
    static readonly JsonSerializerOptions Strict = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        AllowDuplicateProperties = false,
        RespectRequiredConstructorParameters = true,
        RespectNullableAnnotations = true,
    };

    sealed record Entry(string Name, string Shop, IReadOnlyList<string> Secrets);

    sealed record Content(IReadOnlyList<Entry> Feeds);

    public static Feeds Read(string path)
    {
        Content content;
        try
        {
            content = JsonSerializer.Deserialize<Content>(File.ReadAllBytes(path), Strict) ?? throw new JsonException();
        }
        catch (JsonException)
        {
            // Without what went wrong, because that can repeat a part of the file.
            throw new ArgumentException("The file with the feeds cannot be read.");
        }

        return new Feeds(content.Feeds.Select(entry => new Feed(entry.Name, entry.Shop, [.. entry.Secrets.Select(Feeds.ReadSecret)])));
    }
}
