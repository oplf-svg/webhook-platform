namespace Platform.Core;

/// <summary>The most that receiving takes.</summary>
public static class Limits
{
    public const int BodyBytes = 256 * 1024;
    public const int Signatures = 10;
    public static readonly TimeSpan Clock = TimeSpan.FromMinutes(5);
}
