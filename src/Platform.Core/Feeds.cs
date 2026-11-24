using System.Collections.Frozen;
using System.Security.Cryptography;

namespace Platform.Core;

/// <summary>One channel of one shop. Whoever knows one of its secrets can send its events and no others.</summary>
public sealed record Feed(string Name, string Shop, IReadOnlyList<byte[]> Secrets);

public sealed class Feeds
{
    /// <summary>Every feed is checked against this many secrets, whether it has them or not.</summary>
    public const int SecretsOfEach = 3;

    readonly FrozenDictionary<string, Feed> _feeds;

    // Stands in for a feed that does not exist, so that asking for one takes as long as a wrong signature.
    readonly Feed _nobody = new("", "", Filled([]));

    public Feeds(IEnumerable<Feed> feeds)
    {
        var all = feeds.ToList();
        if (all.Count == 0)
            throw new ArgumentException("There is no feed.");
        foreach (var feed in all)
        {
            if (!IsName(feed.Name) || !IsName(feed.Shop))
                throw new ArgumentException("The name of a feed or of its shop is not 1 to 40 of a-z, 0-9 and the hyphen.");
            if (feed.Secrets.Count is 0 or > SecretsOfEach)
                throw new ArgumentException($"The feed {feed.Name} needs 1 to {SecretsOfEach} secrets.");
        }

        // With a shared secret, one feed could sign for the other. Zeros at the end make no difference to the signature, so
        // they make none here.
        var secrets = all.SelectMany(feed => feed.Secrets.Select(secret => Convert.ToHexString(secret.AsSpan().TrimEnd((byte)0)))).ToList();
        if (secrets.Distinct().Count() != secrets.Count)
            throw new ArgumentException("A secret is used twice.");
        _feeds = all.ToFrozenDictionary(feed => feed.Name, feed => feed with { Secrets = Filled(feed.Secrets) }, StringComparer.Ordinal);
    }

    /// <summary>The feed, or one whose secret nobody knows.</summary>
    public Feed Find(string name) => _feeds.GetValueOrDefault(name, _nobody);

    /// <summary>A secret as Standard Webhooks writes it: "whsec_" and 24 to 64 bytes as base64.</summary>
    public static byte[] ReadSecret(string text)
    {
        const string prefix = "whsec_";
        var secret = new byte[64];
        if (!text.StartsWith(prefix, StringComparison.Ordinal)
            || !Convert.TryFromBase64String(text[prefix.Length..], secret, out var length)
            || length < 24)
            throw new ArgumentException("A secret is not \"whsec_\" and 24 to 64 bytes as base64.");
        return secret[..length];
    }

    /// <summary>With as many made-up secrets as are missing, which nobody knows.</summary>
    static List<byte[]> Filled(IReadOnlyList<byte[]> secrets) =>
        [.. secrets, .. Enumerable.Range(0, SecretsOfEach - secrets.Count).Select(_ => RandomNumberGenerator.GetBytes(32))];

    static bool IsName(string name) =>
        name.Length is > 0 and <= 40 && name.All(c => char.IsAsciiLetterLower(c) || char.IsAsciiDigit(c) || c is '-');
}
