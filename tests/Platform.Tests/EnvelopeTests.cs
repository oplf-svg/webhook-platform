using Platform.Core;
using Xunit;

namespace Platform.Tests;

public class EnvelopeTests
{
    static readonly DateTimeOffset Now = DateTimeOffset.FromUnixTimeSeconds(1_800_000_000);

    [Theory]
    [InlineData("a")]
    [InlineData("evt_1:A-b")]
    public void A_plain_id_is_taken(string id) => Assert.NotNull(Envelope.Read([id], ["1"], ["v1,x"]));

    [Theory]
    [InlineData("")]
    [InlineData("a.1")]
    [InlineData("a b")]
    [InlineData("a\n")]
    [InlineData("é")]
    [InlineData("a/b")]
    [InlineData("a\0")]
    public void Any_other_id_is_not(string id) => Assert.Null(Envelope.Read([id], ["1"], ["v1,x"]));

    [Fact]
    public void An_id_has_at_most_100_characters()
    {
        Assert.NotNull(Envelope.Read([new string('a', 100)], ["1"], ["v1,x"]));
        Assert.Null(Envelope.Read([new string('a', 101)], ["1"], ["v1,x"]));
    }

    /// <summary>With a dot in the id, the same signed bytes could be cut in two ways, and one signature would serve both.</summary>
    [Fact]
    public void The_signed_bytes_can_be_cut_in_one_way_only()
    {
        var secret = Sender.NewSecret();
        var signature = Sender.Sign(secret, "a.1800000000", 1_800_000_001, "x"u8.ToArray());
        var cut = new Envelope("a", "1800000000", signature);
        Assert.True(Signature.IsRight([secret], cut, "1800000001.x"u8));
        Assert.Null(Envelope.Read(["a.1800000000"], ["1800000001"], [signature]));
    }

    [Theory]
    [InlineData("")]
    [InlineData("-1")]
    [InlineData("+1")]
    [InlineData(" 1")]
    [InlineData("1.0")]
    [InlineData("1e9")]
    [InlineData("١٢٣")]
    [InlineData("1234567890123")]
    public void A_time_is_digits_only(string time) => Assert.Null(Envelope.Read(["a"], [time], ["v1,x"]));

    [Fact]
    public void Each_header_has_to_be_there_exactly_once()
    {
        Assert.Null(Envelope.Read([], ["1"], ["v1,x"]));
        Assert.Null(Envelope.Read(["a"], [], ["v1,x"]));
        Assert.Null(Envelope.Read(["a"], ["1"], []));
        Assert.Null(Envelope.Read(["a", "b"], ["1"], ["v1,x"]));
        Assert.Null(Envelope.Read(["a"], ["1", "1"], ["v1,x"]));
        Assert.Null(Envelope.Read(["a"], ["1"], ["v1,x", "v1,y"]));
        Assert.Null(Envelope.Read(["a"], ["1"], [""]));
        Assert.Null(Envelope.Read([null], ["1"], ["v1,x"]));
    }

    [Theory]
    [InlineData(0, true)]
    [InlineData(300, true)]
    [InlineData(301, false)]
    [InlineData(-300, true)]
    [InlineData(-301, false)]
    [InlineData(1_800_000_000, false)]
    public void Five_minutes_either_way(long ago, bool current)
    {
        var time = (Now.ToUnixTimeSeconds() - ago).ToString(System.Globalization.CultureInfo.InvariantCulture);
        Assert.Equal(current, new Envelope("a", time, "v1,x").IsCurrent(Now));
    }

    [Fact]
    public void The_largest_time_that_can_be_written_is_only_late() => Assert.False(new Envelope("a", "999999999999", "v1,x").IsCurrent(Now));
}
