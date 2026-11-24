using System.Globalization;

namespace Platform.Core;

/// <summary>What the three headers of a request say. Nothing in it is proven until the signature is.</summary>
public sealed record Envelope(string Id, string Time, string Signatures)
{
    /// <summary>Takes the headers only if each is there once and has the form it must have.</summary>
    public static Envelope? Read(IReadOnlyList<string?> id, IReadOnlyList<string?> time, IReadOnlyList<string?> signatures)
    {
        if (id is not [{ } theId] || time is not [{ } theTime] || signatures is not [{ } theSignatures])
            return null;
        // No dot in the id: the signed text is "id.time.body", and it must not be possible to cut it in two ways.
        if (theId.Length is 0 or > 100 || !theId.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or ':' or '-'))
            return null;
        if (theTime.Length is 0 or > 12 || !theTime.All(char.IsAsciiDigit))
            return null;
        return theSignatures.Length == 0 ? null : new Envelope(theId, theTime, theSignatures);
    }

    public bool IsCurrent(DateTimeOffset now)
    {
        var distance = now.ToUnixTimeSeconds() - long.Parse(Time, CultureInfo.InvariantCulture);
        return Math.Abs(distance) <= (long)Limits.Clock.TotalSeconds;
    }
}
