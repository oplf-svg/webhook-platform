using System.Security.Cryptography;
using System.Text;

namespace Platform.Core;

/// <summary>The signature of Standard Webhooks: HMAC-SHA256 over "id.time.body", with the body exactly as it came.</summary>
public static class Signature
{
    const string Version = "v1,";

    public static byte[] Of(byte[] secret, string id, string time, ReadOnlySpan<byte> body)
    {
        using var hash = IncrementalHash.CreateHMAC(HashAlgorithmName.SHA256, secret);
        hash.AppendData(Encoding.ASCII.GetBytes($"{id}.{time}."));
        hash.AppendData(body);
        return hash.GetHashAndReset();
    }

    /// <summary>True when one of the offered signatures was made with one of the secrets.</summary>
    public static bool IsRight(IReadOnlyList<byte[]> secrets, Envelope envelope, ReadOnlySpan<byte> body)
    {
        var offered = Offered(envelope.Signatures);
        var right = false;
        foreach (var secret in secrets)
        {
            var wanted = Of(secret, envelope.Id, envelope.Time, body);
            foreach (var signature in offered)
                right |= CryptographicOperations.FixedTimeEquals(wanted, signature);
        }

        return right;
    }

    static List<byte[]> Offered(string header)
    {
        var offered = new List<byte[]>();
        foreach (var part in header.Split(' ', Limits.Signatures + 1).Take(Limits.Signatures))
        {
            var signature = new byte[HMACSHA256.HashSizeInBytes];
            // Other versions may stand beside ours, so anything else is passed over and not an error.
            if (part.StartsWith(Version, StringComparison.Ordinal)
                && Convert.TryFromBase64String(part[Version.Length..], signature, out var length)
                && length == signature.Length)
                offered.Add(signature);
        }

        return offered;
    }
}
