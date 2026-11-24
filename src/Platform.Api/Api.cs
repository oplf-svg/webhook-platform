using Npgsql;
using Platform.Core;
using Platform.Data;

namespace Platform.Api;

public static partial class Api
{
    public static WebApplication Build(string[] args, TimeProvider? clock = null)
    {
        var builder = WebApplication.CreateSlimBuilder(args);
        builder.Logging.ClearProviders().AddJsonConsole().AddFilter("Microsoft", LogLevel.Warning);
        builder.WebHost.ConfigureKestrel(server =>
        {
            server.AddServerHeader = false;
            server.Limits.MaxRequestBodySize = Limits.BodyBytes;
            server.Limits.MaxRequestHeadersTotalSize = 8 * 1024;
            server.Limits.MaxRequestHeaderCount = 40;
            server.Limits.RequestHeadersTimeout = TimeSpan.FromSeconds(5);
        });

        var source = Database.Open(Setting("PLATFORM_DATABASE"), "api");
        builder.Services.AddSingleton(source);
        builder.Services.AddSingleton(FeedFile.Read(Setting("PLATFORM_FEEDS")));
        builder.Services.AddSingleton(clock ?? TimeProvider.System);
        builder.Services.AddSingleton<Inbox>();
        builder.Services.AddSingleton<Refusals>();
        builder.Services.AddSingleton<Receiver>();

        var app = builder.Build();
        // Made now, so that a setting, a feed or a statement that is missing stops the start and not the first event.
        var inbox = app.Services.GetRequiredService<Inbox>();
        inbox.ReadyToStore();
        var receiver = app.Services.GetRequiredService<Receiver>();
        app.MapPost("/hooks/{feed}", receiver.ReceiveAsync);
        app.MapGet("/health/live", () => Results.Ok());
        app.MapGet("/health/ready", async () =>
        {
            try
            {
                await inbox.AskAsync();
                return Results.Ok();
            }
            catch (Exception problem) when (problem is NpgsqlException or TimeoutException)
            {
                NotReady(app.Logger, problem);
                return Results.StatusCode(StatusCodes.Status503ServiceUnavailable);
            }
        });
        return app;

        string Setting(string name) =>
            builder.Configuration[name] is { Length: > 0 } value ? value : throw new InvalidOperationException($"{name} is not set.");
    }

    [LoggerMessage(LogLevel.Error, "not ready")]
    static partial void NotReady(ILogger log, Exception problem);
}
