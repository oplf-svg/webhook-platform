using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.DependencyInjection;
using Xunit;

namespace Platform.Tests;

/// <summary>Every answer that the contract names, over real HTTP. Refused means that no trace is stored.</summary>
public sealed class ApiTests(TestDatabase database) : IClassFixture<TestDatabase>, IAsyncLifetime
{
    static readonly byte[] North = Sender.NewSecret();
    static readonly byte[] NorthNext = Sender.NewSecret();
    static readonly byte[] South = Sender.NewSecret();

    readonly string _feeds = Path.Combine(Path.GetTempPath(), $"feeds-{Guid.NewGuid():N}.json");
    readonly HttpClient _client = new();
    readonly List<WebApplication> _apps = [];
    Uri _address = null!;

    public async ValueTask InitializeAsync()
    {
        await database.RunAsync("truncate inbox, conflicts, orders, totals");
        await File.WriteAllTextAsync(_feeds, $$"""
            {"feeds":[
              {"name":"north-web","shop":"north","secrets":["{{Sender.Written(North)}}","{{Sender.Written(NorthNext)}}"]},
              {"name":"south-web","shop":"south","secrets":["{{Sender.Written(South)}}"]}]}
            """);
        _address = await StartAsync(database.Connection);
    }

    public async ValueTask DisposeAsync()
    {
        foreach (var app in _apps)
            await app.DisposeAsync();
        _client.Dispose();
        File.Delete(_feeds);
    }

    async Task<Uri> StartAsync(string connection, TimeProvider? clock = null)
    {
        var app = Platform.Api.Api.Build([$"--PLATFORM_DATABASE={connection}", $"--PLATFORM_FEEDS={_feeds}", "--urls=http://127.0.0.1:0"], clock);
        _apps.Add(app);
        await app.StartAsync();
        return new Uri(app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single());
    }

    static long Now => DateTimeOffset.UtcNow.ToUnixTimeSeconds();

    HttpRequestMessage Request(string id, byte[] body, byte[]? secret = null, string feed = "north-web", long? time = null)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, new Uri(_address, $"/hooks/{feed}")) { Content = new ByteArrayContent(body) };
        var at = time ?? Now;
        Set(request, "webhook-id", id);
        Set(request, "webhook-timestamp", at.ToString(CultureInfo.InvariantCulture));
        Set(request, "webhook-signature", Sender.Sign(secret ?? North, id, at, body));
        return request;
    }

    static void Set(HttpRequestMessage request, string name, params string[] values)
    {
        request.Headers.Remove(name);
        Assert.True(request.Headers.TryAddWithoutValidation(name, values));
    }

    async Task<(HttpStatusCode Status, string Text)> Send(HttpRequestMessage request)
    {
        using (request)
        {
            using var response = await _client.SendAsync(request);
            return (response.StatusCode, await response.Content.ReadAsStringAsync());
        }
    }

    async Task Refused(HttpStatusCode status, string text, HttpRequestMessage request)
    {
        Assert.Equal((status, $$"""{"result":"{{text}}"}"""), await Send(request));
        Assert.Equal(0, await database.AskAsync<long>("select (select count(*) from inbox) + (select count(*) from conflicts)"));
    }

    Task Refused401(HttpRequestMessage request) => Refused(HttpStatusCode.Unauthorized, "refused", request);

    Task Refused400(HttpRequestMessage request) => Refused(HttpStatusCode.BadRequest, "malformed", request);

    [Fact]
    public async Task A_new_event_a_known_one_and_another_body()
    {
        var body = Sender.Order("N-1");
        Assert.Equal((HttpStatusCode.Accepted, """{"result":"stored"}"""), await Send(Request("e1", body)));
        Assert.Equal((HttpStatusCode.OK, """{"result":"already here"}"""), await Send(Request("e1", body, time: Now - 100)));
        Assert.Equal((HttpStatusCode.Conflict, """{"result":"another body is here under this id"}"""), await Send(Request("e1", Sender.Order("N-2"))));
        Assert.Equal("north-web north 2", await database.AskAsync<string>("select concat_ws(' ', feed, shop, (select count(*) from conflicts) + 1) from inbox"));
        Assert.Equal(body, await database.AskAsync<byte[]>("select body from inbox"));
    }

    [Fact]
    public async Task Both_secrets_of_a_feed_are_good_while_it_changes_them()
    {
        Assert.Equal(HttpStatusCode.Accepted, (await Send(Request("e1", Sender.Order("N-1")))).Status);
        Assert.Equal(HttpStatusCode.Accepted, (await Send(Request("e2", Sender.Order("N-2"), NorthNext))).Status);
    }

    [Fact]
    public async Task A_body_is_stored_byte_for_byte_whatever_it_is()
    {
        byte[][] bodies = [[], [0, 0xff, 10, 13], [.. Enumerable.Repeat((byte)'x', 256 * 1024)]];
        foreach (var (body, number) in bodies.Select((body, number) => (body, number)))
        {
            Assert.Equal(HttpStatusCode.Accepted, (await Send(Request($"b{number}", body))).Status);
            Assert.Equal(body, await database.AskAsync<byte[]>("select body from inbox where event_id = $1", $"b{number}"));
        }
    }

    [Fact]
    public async Task Forged_events_are_refused()
    {
        var body = Sender.Order("N-1");
        await Refused401(Request("e1", body, Sender.NewSecret()));
        // Another feed's secret, in both directions.
        await Refused401(Request("e1", body, South));
        await Refused401(Request("e1", body, North, "south-web"));

        var changed = Request("e1", body);
        changed.Content = new ByteArrayContent(Sender.Order("N-2"));
        await Refused401(changed);

        var renamed = Request("e1", body);
        Set(renamed, "webhook-id", "e2");
        await Refused401(renamed);

        var moved = Request("e1", body);
        Set(moved, "webhook-timestamp", (Now + 7).ToString(CultureInfo.InvariantCulture));
        await Refused401(moved);

        var other = Request("e1", body);
        Set(other, "webhook-signature", "v1a,AAAA");
        await Refused401(other);
    }

    [Fact]
    public async Task A_real_event_is_taken_after_a_forged_one_with_its_id()
    {
        await Refused401(Request("e1", Sender.Order("N-66"), South));
        Assert.Equal(HttpStatusCode.Accepted, (await Send(Request("e1", Sender.Order("N-1")))).Status);
    }

    [Fact]
    public async Task What_was_recorded_too_long_ago_or_comes_from_the_future_is_refused()
    {
        await Refused401(Request("e1", Sender.Order("N-1"), time: Now - 310));
        await Refused401(Request("e1", Sender.Order("N-1"), time: Now + 310));
    }

    sealed class LaterClock : TimeProvider
    {
        public TimeSpan Later { get; set; }

        public override DateTimeOffset GetUtcNow() => DateTimeOffset.UtcNow + Later;
    }

    [Fact]
    public async Task A_body_that_takes_its_time_does_not_make_a_request_younger()
    {
        var clock = new LaterClock();
        _address = await StartAsync(database.Connection, clock);
        var body = Sender.Order("N-1");
        var at = Now - 290;
        using var socket = new TcpClient();
        await socket.ConnectAsync(_address.Host, _address.Port);
        var head = $"POST /hooks/north-web HTTP/1.1\r\nHost: x\r\nConnection: close\r\nwebhook-id: e1\r\nwebhook-timestamp: {at}\r\nwebhook-signature: {Sender.Sign(North, "e1", at, body)}\r\nContent-Length: {body.Length}\r\n\r\n";
        await socket.GetStream().WriteAsync(Encoding.ASCII.GetBytes(head));
        await socket.GetStream().WriteAsync(body.AsMemory(0, 20));
        await Task.Delay(300);
        clock.Later = TimeSpan.FromSeconds(30);
        await socket.GetStream().WriteAsync(body.AsMemory(20));
        using var reader = new StreamReader(socket.GetStream());
        Assert.StartsWith("HTTP/1.1 401 ", await reader.ReadToEndAsync(), StringComparison.Ordinal);
        Assert.Equal(0, await database.AskAsync<long>("select count(*) from inbox"));
    }

    [Fact]
    public async Task Without_its_tables_it_is_not_ready_and_does_not_call_it_an_outage()
    {
        var empty = new TestDatabase();
        await empty.InitializeAsync();
        try
        {
            await empty.RunAsync("drop table conflicts, inbox");
            _address = await StartAsync(empty.Connection);
            Assert.Equal(HttpStatusCode.ServiceUnavailable, (await Send(new HttpRequestMessage(HttpMethod.Get, new Uri(_address, "/health/ready")))).Status);
            Assert.Equal(HttpStatusCode.InternalServerError, (await Send(Request("e1", Sender.Order("N-1")))).Status);
        }
        finally
        {
            await empty.DisposeAsync();
        }
    }

    [Fact]
    public async Task A_feed_that_does_not_exist_gets_the_answer_of_a_wrong_signature()
    {
        await Refused401(Request("e1", Sender.Order("N-1"), feed: "nobody"));
        await Refused401(Request("e1", Sender.Order("N-1"), feed: "North-Web"));
        await Refused401(Request("e1", Sender.Order("N-1"), feed: "north-web%2F"));
    }

    [Fact]
    public async Task Headers_that_are_missing_doubled_or_malformed_are_refused()
    {
        foreach (var name in new[] { "webhook-id", "webhook-timestamp", "webhook-signature" })
        {
            var missing = Request("e1", Sender.Order("N-1"));
            missing.Headers.Remove(name);
            await Refused400(missing);

            var doubled = Request("e1", Sender.Order("N-1"));
            Set(doubled, name, doubled.Headers.GetValues(name).Single(), "x");
            // The client joins them into one line, which is then one malformed value or one wrong signature.
            Assert.Contains((await Send(doubled)).Status, new[] { HttpStatusCode.BadRequest, HttpStatusCode.Unauthorized });
        }

        await Refused400(Request("a.1", Sender.Order("N-1")));
        await Refused400(Request(new string('a', 101), Sender.Order("N-1")));
        var time = Request("e1", Sender.Order("N-1"));
        Set(time, "webhook-timestamp", "now");
        await Refused400(time);
    }

    [Fact]
    public async Task Headers_that_are_really_sent_twice_are_refused()
    {
        var body = Sender.Order("N-1");
        var at = Now;
        var signature = Sender.Sign(North, "e1", at, body);
        var answer = await Raw($"POST /hooks/north-web HTTP/1.1\r\nHost: x\r\nConnection: close\r\nwebhook-id: e1\r\nwebhook-id: e1\r\nwebhook-timestamp: {at}\r\nwebhook-signature: {signature}\r\nContent-Length: {body.Length}\r\n\r\n", body);
        Assert.StartsWith("HTTP/1.1 400 ", answer, StringComparison.Ordinal);
        Assert.Equal(0, await database.AskAsync<long>("select count(*) from inbox"));
    }

    [Fact]
    public async Task A_body_that_is_too_large_is_refused_with_and_without_a_length()
    {
        var body = new byte[256 * 1024 + 1];
        await Refused(HttpStatusCode.RequestEntityTooLarge, "too large", Request("e1", body));
        var chunked = Request("e1", body);
        chunked.Headers.TransferEncodingChunked = true;
        await Refused(HttpStatusCode.RequestEntityTooLarge, "too large", chunked);
    }

    [Fact]
    public async Task A_body_that_ends_too_early_is_not_stored()
    {
        var body = Sender.Order("N-1");
        var at = Now;
        using (var socket = new TcpClient())
        {
            await socket.ConnectAsync(_address.Host, _address.Port);
            var head = $"POST /hooks/north-web HTTP/1.1\r\nHost: x\r\nwebhook-id: e1\r\nwebhook-timestamp: {at}\r\nwebhook-signature: {Sender.Sign(North, "e1", at, body[..20])}\r\nContent-Length: {body.Length}\r\n\r\n";
            await socket.GetStream().WriteAsync(Encoding.ASCII.GetBytes(head));
            await socket.GetStream().WriteAsync(body.AsMemory(0, 20));
        }

        await Task.Delay(300);
        Assert.Equal(0, await database.AskAsync<long>("select count(*) from inbox"));
        Assert.Equal(HttpStatusCode.Accepted, (await Send(Request("e1", body))).Status);
    }

    [Fact]
    public async Task Only_one_way_leads_in()
    {
        Assert.Equal(HttpStatusCode.MethodNotAllowed, (await Send(new HttpRequestMessage(HttpMethod.Get, new Uri(_address, "/hooks/north-web")))).Status);
        Assert.Equal(HttpStatusCode.NotFound, (await Send(new HttpRequestMessage(HttpMethod.Post, new Uri(_address, "/hooks")))).Status);
        Assert.Equal(HttpStatusCode.NotFound, (await Send(new HttpRequestMessage(HttpMethod.Post, new Uri(_address, "/hooks/north-web/more")))).Status);
        Assert.Equal(HttpStatusCode.NotFound, (await Send(new HttpRequestMessage(HttpMethod.Get, _address))).Status);
        using var response = await _client.GetAsync(new Uri(_address, "/health/live"));
        Assert.False(response.Headers.Contains("Server"));
    }

    [Fact]
    public async Task Without_the_database_nothing_is_accepted()
    {
        _address = await StartAsync("Host=127.0.0.1;Port=1;Username=nobody;Password=nothing;Database=nowhere");
        using var request = Request("e1", Sender.Order("N-1"));
        using var response = await _client.SendAsync(request);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.Equal("2", response.Headers.GetValues("Retry-After").Single());
        Assert.Equal(HttpStatusCode.OK, (await Send(new HttpRequestMessage(HttpMethod.Get, new Uri(_address, "/health/live")))).Status);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, (await Send(new HttpRequestMessage(HttpMethod.Get, new Uri(_address, "/health/ready")))).Status);
        // A forged one is refused as always, so the answer does not depend on the database before the signature is right.
        Assert.Equal(HttpStatusCode.Unauthorized, (await Send(Request("e1", Sender.Order("N-1"), South))).Status);
    }

    [Fact]
    public async Task With_the_database_it_is_ready() =>
        Assert.Equal(HttpStatusCode.OK, (await Send(new HttpRequestMessage(HttpMethod.Get, new Uri(_address, "/health/ready")))).Status);

    [Theory]
    [InlineData("")]
    [InlineData("{}")]
    [InlineData("""{"feeds":[]}""")]
    [InlineData("""{"feeds":[{"name":"a","shop":"b"}]}""")]
    [InlineData("""{"feeds":[{"name":"a","shop":"b","secrets":null}]}""")]
    [InlineData("""{"feeds":[{"name":"a","shop":"b","secrets":["whsec_secret-but-wrong"]}]}""")]
    [InlineData("""{"feeds":[],"more":1}""")]
    public async Task With_a_wrong_file_of_feeds_it_does_not_start_and_does_not_repeat_it(string content)
    {
        await File.WriteAllTextAsync(_feeds, content);
        var problem = await Assert.ThrowsAsync<ArgumentException>(() => StartAsync(database.Connection));
        Assert.DoesNotContain("secret-but-wrong", problem.ToString(), StringComparison.Ordinal);
    }

    async Task<string> Raw(string head, byte[] body)
    {
        using var socket = new TcpClient();
        await socket.ConnectAsync(_address.Host, _address.Port);
        await socket.GetStream().WriteAsync(Encoding.ASCII.GetBytes(head));
        await socket.GetStream().WriteAsync(body);
        using var reader = new StreamReader(socket.GetStream());
        return await reader.ReadToEndAsync();
    }
}
