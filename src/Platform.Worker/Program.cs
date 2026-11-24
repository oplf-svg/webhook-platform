using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Platform.Data;
using Platform.Worker;

var builder = Host.CreateEmptyApplicationBuilder(null);
builder.Configuration.AddEnvironmentVariables();
builder.Logging.AddJsonConsole().AddFilter("Microsoft", LogLevel.Warning);
var connection = builder.Configuration["PLATFORM_DATABASE"] is { Length: > 0 } value ? value : throw new InvalidOperationException("PLATFORM_DATABASE is not set.");

switch (args)
{
    case ["migrate"]:
        await using (var source = Database.Open(connection, "migrate"))
            await Migrator.ApplyAsync(source, CancellationToken.None);
        return 0;

    case []:
        builder.Services.AddSingleton(Database.Open(connection, "worker"));
        builder.Services.AddSingleton(WorkerSettings.Read(builder.Configuration));
        builder.Services.AddSingleton<Inbox>();
        builder.Services.AddSingleton<Ledger>();
        builder.Services.AddHostedService<EventWorker>();
        builder.Services.Configure<HostOptions>(options => options.ShutdownTimeout = TimeSpan.FromSeconds(15));
        var host = builder.Build();
        host.Services.GetRequiredService<Inbox>().ReadyToWork();
        var worker = host.Services.GetServices<IHostedService>().OfType<EventWorker>().Single();
        await host.RunAsync();
        // A worker that broke, or that could not finish what it had, must not look as if all was well.
        return worker.ExecuteTask is { IsCompletedSuccessfully: true } ? 0 : 1;

    default:
        await Console.Error.WriteLineAsync("There are two ways to start this: with \"migrate\", or with nothing.");
        return 2;
}
