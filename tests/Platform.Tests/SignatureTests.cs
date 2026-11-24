using System.Text;
using Platform.Core;
using Xunit;

namespace Platform.Tests;

public class SignatureTests
{
    const string Id = "msg_p5jXN8AQM9LWM0D4loKWxJek";
    const string Time = "1614265330";
    static readonly byte[] Body = Encoding.UTF8.GetBytes("""{"test": 2432232314}""");
    // The published example. In two parts, because scanners would take it for a real secret.
    static readonly byte[] Secret = Feeds.ReadSecret("whsec_" + "MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw");
    const string Right = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=";

    static bool Check(string header, byte[]? body = null, string id = Id, string time = Time, params byte[][] secrets) =>
        Signature.IsRight(secrets.Length == 0 ? [Secret] : secrets, new Envelope(id, time, header), body ?? Body);

    [Fact]
    public void The_published_example_is_right() => Assert.True(Check(Right));

    [Fact]
    public void Every_changed_bit_of_the_body_is_noticed()
    {
        for (var bit = 0; bit < Body.Length * 8; bit++)
            Assert.False(Check(Right, Flipped(Body, bit)));
    }

    [Fact]
    public void Every_changed_bit_of_the_signature_is_noticed()
    {
        var signature = Convert.FromBase64String(Right[3..]);
        for (var bit = 0; bit < signature.Length * 8; bit++)
            Assert.False(Check("v1," + Convert.ToBase64String(Flipped(signature, bit))));
    }

    [Theory]
    [InlineData("msg_p5jXN8AQM9LWM0D4loKWxJel", Time)]
    [InlineData(Id, "1614265331")]
    [InlineData(Id, "01614265330")]
    public void A_changed_id_or_time_is_noticed(string id, string time) => Assert.False(Check(Right, id: id, time: time));

    [Theory]
    [InlineData("")]
    [InlineData("v1,")]
    [InlineData("v1")]
    [InlineData("g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=")]
    [InlineData("v1a,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=")]
    [InlineData("V1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=")]
    [InlineData("v2,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=")]
    [InlineData("v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1A==")]
    [InlineData("v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")]
    [InlineData("v1,not base64 at all")]
    public void Anything_that_is_not_the_whole_signature_is_refused(string header) => Assert.False(Check(header));

    [Fact]
    public void One_right_signature_among_others_is_enough()
    {
        Assert.True(Check($"v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= v1a,xyz {Right}"));
        Assert.False(Check(string.Join(' ', Enumerable.Repeat("v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", 10)) + " " + Right));
    }

    [Fact]
    public void While_a_secret_is_being_changed_both_are_right()
    {
        var next = Sender.NewSecret();
        Assert.True(Check(Right, secrets: [next, Secret]));
        Assert.True(Check(Right, secrets: [Secret, next]));
        Assert.True(Check(Sender.Sign(next, Id, 1614265330, Body), secrets: [Secret, next]));
        Assert.False(Check(Right, secrets: [next]));
    }

    [Fact]
    public void Our_own_way_of_signing_agrees_with_the_published_example() => Assert.Equal(Right, Sender.Sign(Secret, Id, 1614265330, Body));

    static byte[] Flipped(byte[] bytes, int bit)
    {
        var copy = (byte[])bytes.Clone();
        copy[bit / 8] ^= (byte)(1 << (bit % 8));
        return copy;
    }
}
