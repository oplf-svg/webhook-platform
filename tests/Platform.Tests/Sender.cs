using System.Security.Cryptography;
using System.Text;

namespace Platform.Tests;

/// <summary>Signs like a feed would. Written on its own, so that a mistake in our check is not repeated here.</summary>
static class Sender
{
    public static byte[] NewSecret() => RandomNumberGenerator.GetBytes(32);

    public static string Written(byte[] secret) => "whsec_" + Convert.ToBase64String(secret);

    public static string Sign(byte[] secret, string id, long time, byte[] body)
    {
        var signed = Encoding.UTF8.GetBytes($"{id}.{time}.").Concat(body).ToArray();
        return "v1," + Convert.ToBase64String(HMACSHA256.HashData(secret, signed));
    }

    public static byte[] Order(string number, string sku = "BOX-S", int quantity = 1, int cents = 1250) => Encoding.UTF8.GetBytes(
        $$"""{"type":"order.placed","order":"{{number}}","sku":"{{sku}}","quantity":{{quantity}},"cents":{{cents}},"placed_at":"2026-11-24T10:00:00Z"}""");
}
