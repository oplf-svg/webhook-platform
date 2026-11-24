using System.Globalization;
using System.Text.Json;

namespace Platform.Core;

/// <summary>An event that nobody will ever understand, so there is no point in trying it again.</summary>
public sealed class UnreadableException(string reason) : Exception(reason);

/// <summary>What an event says. The shop is not in it: that comes from the feed.</summary>
public sealed record Order(string Number, string Sku, int Quantity, long Cents, DateTimeOffset PlacedAt)
{
    /// <summary>Reads the body. The reasons never repeat anything that stood in it.</summary>
    public static Order Read(ReadOnlyMemory<byte> body)
    {
        JsonDocument json;
        try
        {
            json = JsonDocument.Parse(body, new JsonDocumentOptions { AllowDuplicateProperties = false, MaxDepth = 8 });
        }
        catch (Exception problem) when (problem is JsonException or InvalidOperationException)
        {
            // The second one is what the reader throws for a name that is not text.
            throw new UnreadableException("not JSON, or a name in it stands twice");
        }

        using (json)
        {
            try
            {
                var root = json.RootElement;
                if (root.ValueKind != JsonValueKind.Object)
                    throw new UnreadableException("not an object");
                if (Text(root, "type") != "order.placed")
                    throw new UnreadableException("not a type that is known");
                return new Order(Code(root, "order"), Code(root, "sku"), Whole(root, "quantity", 1, 999), Whole(root, "cents", 0, 100_000_000), Moment(root, "placed_at"));
            }
            catch (InvalidOperationException)
            {
                // What the reader throws for text that is not UTF-8.
                throw new UnreadableException("not text");
            }
        }
    }

    static string Text(JsonElement root, string name) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString()! : throw Wrong(name);

    static string Code(JsonElement root, string name)
    {
        var text = Text(root, name);
        return text.Length is > 0 and <= 40 && text.All(c => char.IsAsciiLetterOrDigit(c) || c is '-') ? text : throw Wrong(name);
    }

    static int Whole(JsonElement root, string name, int least, int most) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number) && number >= least && number <= most
            ? number
            : throw Wrong(name);

    static DateTimeOffset Moment(JsonElement root, string name) =>
        DateTimeOffset.TryParseExact(Text(root, name), "yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var moment)
            ? moment
            : throw Wrong(name);

    static UnreadableException Wrong(string name) => new($"missing or wrong: {name}");
}
