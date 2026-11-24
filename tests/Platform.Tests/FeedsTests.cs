using Platform.Core;
using Xunit;

namespace Platform.Tests;

public class FeedsTests
{
    static Feed Feed(string name = "north-web", string shop = "north", params byte[][] secrets) =>
        new(name, shop, secrets.Length == 0 ? [Sender.NewSecret()] : secrets);

    [Fact]
    public void A_feed_is_found_by_its_exact_name()
    {
        var feeds = new Feeds([Feed(), Feed("south-web", "south")]);
        Assert.Equal(("north-web", "north"), (feeds.Find("north-web").Name, feeds.Find("north-web").Shop));
        Assert.Equal("", feeds.Find("North-Web").Name);
    }

    [Fact]
    public void Nobody_stands_in_for_a_feed_that_does_not_exist()
    {
        var one = new Feeds([Feed()]).Find("nobody");
        var other = new Feeds([Feed()]).Find("nobody");
        Assert.Equal("", one.Shop);
        // Its secrets are made anew each time, so nobody can know them.
        Assert.Equal(6, one.Secrets.Concat(other.Secrets).Select(Convert.ToHexString).Distinct().Count());
        Assert.All(one.Secrets, secret => Assert.Equal(32, secret.Length));
    }

    [Fact]
    public void Every_feed_is_checked_against_as_many_secrets()
    {
        var secrets = new[] { Sender.NewSecret(), Sender.NewSecret(), Sender.NewSecret() };
        var feeds = new Feeds([Feed(secrets: secrets[0]), Feed("south-web", "south", secrets[1], secrets[2])]);
        Assert.Equal([3, 3, 3], new[] { "north-web", "south-web", "nobody" }.Select(name => feeds.Find(name).Secrets.Count));
        Assert.Equal(secrets[0], feeds.Find("north-web").Secrets[0]);
        Assert.Equal([secrets[1], secrets[2]], feeds.Find("south-web").Secrets.Take(2));
    }

    [Fact]
    public void Zeros_at_the_end_of_a_secret_make_no_difference()
    {
        var secret = Sender.NewSecret();
        Assert.Throws<ArgumentException>(() => new Feeds([Feed(secrets: secret), Feed("south-web", "south", [.. secret, 0])]));
        Assert.Throws<ArgumentException>(() => new Feeds([Feed(secrets: [[.. secret, 0, 0], [.. secret, 0]])]));
    }

    [Fact]
    public void Two_feeds_cannot_share_a_secret()
    {
        var secret = Sender.NewSecret();
        Assert.Throws<ArgumentException>(() => new Feeds([Feed(secrets: secret), Feed("south-web", "south", secret)]));
        Assert.Throws<ArgumentException>(() => new Feeds([Feed(secrets: [secret, (byte[])secret.Clone()])]));
    }

    [Fact]
    public void Two_feeds_cannot_share_a_name() => Assert.Throws<ArgumentException>(() => new Feeds([Feed(), Feed()]));

    [Theory]
    [InlineData("")]
    [InlineData("North")]
    [InlineData("north/web")]
    [InlineData("north web")]
    [InlineData("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    public void Names_are_plain(string name)
    {
        Assert.Throws<ArgumentException>(() => new Feeds([Feed(name)]));
        Assert.Throws<ArgumentException>(() => new Feeds([Feed(shop: name)]));
    }

    [Fact]
    public void There_has_to_be_a_feed_and_it_has_one_to_three_secrets()
    {
        Assert.Throws<ArgumentException>(() => new Feeds([]));
        Assert.Throws<ArgumentException>(() => new Feeds([new Feed("north-web", "north", [])]));
        Assert.Throws<ArgumentException>(() => new Feeds([Feed(secrets: [Sender.NewSecret(), Sender.NewSecret(), Sender.NewSecret(), Sender.NewSecret()])]));
    }

    [Theory]
    [InlineData("")]
    [InlineData("whsec_")]
    [InlineData("whsec_c2hvcnQ=")]
    [InlineData("MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw")]
    [InlineData("whsec_not base64")]
    public void A_secret_is_long_enough_and_written_the_usual_way(string text)
    {
        var problem = Assert.Throws<ArgumentException>(() => Feeds.ReadSecret(text));
        if (text.Length > 10)
            Assert.DoesNotContain(text, problem.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void A_secret_has_at_most_64_bytes()
    {
        Assert.Equal(64, Feeds.ReadSecret("whsec_" + Convert.ToBase64String(new byte[64])).Length);
        Assert.Throws<ArgumentException>(() => Feeds.ReadSecret("whsec_" + Convert.ToBase64String(new byte[65])));
    }
}
