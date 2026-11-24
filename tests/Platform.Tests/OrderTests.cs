using System.Text;
using Platform.Core;
using Xunit;

namespace Platform.Tests;

public class OrderTests
{
    const string Good = """{"type":"order.placed","order":"N-1","sku":"BOX-S","quantity":2,"cents":1250,"placed_at":"2026-11-24T10:00:00Z"}""";

    static string Reason(byte[] body) => Assert.Throws<UnreadableException>(() => Order.Read(body)).Message;

    static string Reason(string body) => Reason(Encoding.UTF8.GetBytes(body));

    [Fact]
    public void An_order_is_read() =>
        Assert.Equal(new Order("N-1", "BOX-S", 2, 1250, new DateTimeOffset(2026, 11, 24, 10, 0, 0, TimeSpan.Zero)), Order.Read(Encoding.UTF8.GetBytes(Good)));

    [Fact]
    public void A_field_that_is_not_known_is_passed_over() =>
        Assert.Equal("N-1", Order.Read(Encoding.UTF8.GetBytes(Good.Replace("{", """{"note":"x",""", StringComparison.Ordinal))).Number);

    [Theory]
    [InlineData("", "not JSON, or a name in it stands twice")]
    [InlineData("{", "not JSON, or a name in it stands twice")]
    [InlineData("[[[[[[[[[[1]]]]]]]]]]", "not JSON, or a name in it stands twice")]
    [InlineData("{\"\\ud800\":1}", "not JSON, or a name in it stands twice")]
    [InlineData("{\"type\":\"\\ud800\"}", "not text")]
    [InlineData("null", "not an object")]
    [InlineData("[]", "not an object")]
    [InlineData("\"order.placed\"", "not an object")]
    [InlineData("{}", "missing or wrong: type")]
    public void What_is_no_order_at_all_is_unreadable(string body, string reason) => Assert.Equal(reason, Reason(body));

    [Fact]
    public void A_name_that_stands_twice_is_unreadable() =>
        Assert.Equal("not JSON, or a name in it stands twice", Reason(Good.Replace("\"cents\":1250", "\"cents\":1250,\"cents\":1", StringComparison.Ordinal)));

    [Fact]
    public void Another_type_is_unreadable() => Assert.Equal("not a type that is known", Reason(Good.Replace("placed", "eaten", StringComparison.Ordinal)));

    [Theory]
    [InlineData("order", "\"\"")]
    [InlineData("order", "\"N 1\"")]
    [InlineData("order", "\"N\\u0000\"")]
    [InlineData("order", "\"NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN\"")]
    [InlineData("order", "1")]
    [InlineData("order", "null")]
    [InlineData("sku", "[\"BOX-S\"]")]
    [InlineData("quantity", "0")]
    [InlineData("quantity", "-1")]
    [InlineData("quantity", "1000")]
    [InlineData("quantity", "1.5")]
    [InlineData("quantity", "\"2\"")]
    [InlineData("quantity", "99999999999999999999")]
    [InlineData("cents", "-1")]
    [InlineData("cents", "100000001")]
    [InlineData("placed_at", "\"yesterday\"")]
    [InlineData("placed_at", "\"2026-11-24T10:00:00\"")]
    [InlineData("placed_at", "\"2026-11-24T10:00:00+01:00\"")]
    [InlineData("placed_at", "\"2026-02-30T10:00:00Z\"")]
    public void A_wrong_field_is_named_and_its_content_is_not(string name, string value)
    {
        var fields = new Dictionary<string, string>
        {
            ["type"] = "\"order.placed\"", ["order"] = "\"N-1\"", ["sku"] = "\"BOX-S\"", ["quantity"] = "2", ["cents"] = "1250", ["placed_at"] = "\"2026-11-24T10:00:00Z\"",
        };
        fields[name] = value;
        Assert.Equal($"missing or wrong: {name}", Reason("{" + string.Join(',', fields.Select(field => $"\"{field.Key}\":{field.Value}")) + "}"));
        fields.Remove(name);
        Assert.Equal($"missing or wrong: {name}", Reason("{" + string.Join(',', fields.Select(field => $"\"{field.Key}\":{field.Value}")) + "}"));
    }

    [Fact]
    public void Text_that_is_not_UTF_8_is_unreadable()
    {
        var body = Encoding.UTF8.GetBytes(Good);
        body[Array.IndexOf(body, (byte)'N')] = 0xff;
        Assert.Equal("not text", Reason(body));
    }

    /// <summary>Whatever comes, it is an order or unreadable. Any other end would be tried again and again.</summary>
    [Fact]
    public void Nothing_else_can_come_out_of_it()
    {
        var seed = Environment.TickCount;
        var random = new Random(seed);
        var pieces = new[] { "\"", "{", "}", "[", "]", ",", ":", "\\", "\\u0000", "\\ud800", "\\udc00", "1e999", "-0", "\u00e9", "null", "true", "9999999999" }.Select(Encoding.UTF8.GetBytes).ToArray();
        for (var round = 0; round < 60_000; round++)
        {
            var body = Encoding.UTF8.GetBytes(Good).ToList();
            for (var change = random.Next(1, 5); change > 0; change--)
            {
                var at = random.Next(body.Count);
                switch (random.Next(4))
                {
                    case 0:
                        body[at] = (byte)random.Next(256);
                        break;
                    case 1:
                        body.RemoveRange(at, Math.Min(random.Next(1, 6), body.Count - at));
                        break;
                    case 2:
                        body.InsertRange(at, pieces[random.Next(pieces.Length)]);
                        break;
                    default:
                        body.InsertRange(at, body.GetRange(at, Math.Min(random.Next(1, 30), body.Count - at)));
                        break;
                }

                if (body.Count == 0)
                    body.Add(0);
            }

            try
            {
                Order.Read(body.ToArray());
            }
            catch (UnreadableException)
            {
            }
            catch (Exception problem)
            {
                Assert.Fail($"seed {seed}, round {round}: {problem.GetType().Name} for {Convert.ToHexString(body.ToArray())}");
            }
        }
    }
}
