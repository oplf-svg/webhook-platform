using Platform.Data;
using Xunit;

namespace Platform.Tests;

public sealed class MigratorTests(TestDatabase database) : IClassFixture<TestDatabase>
{
    [Fact]
    public async Task Applying_again_changes_nothing()
    {
        var before = await database.ListAsync<string>("select name || hash || applied_at from schema_files order by name");
        Assert.NotEmpty(before);
        await using var source = Database.Open(database.Connection, "test");
        await Task.WhenAll(Enumerable.Range(0, 4).Select(_ => Migrator.ApplyAsync(source, CancellationToken.None)));
        Assert.Equal(before, await database.ListAsync<string>("select name || hash || applied_at from schema_files order by name"));
    }

    [Fact]
    public async Task A_file_that_was_changed_later_is_noticed()
    {
        await using var source = Database.Open(database.Connection, "test");
        await database.RunAsync("update schema_files set hash = 'x' || hash where name = '002_orders.sql'");
        try
        {
            var problem = await Assert.ThrowsAsync<InvalidOperationException>(() => Migrator.ApplyAsync(source, CancellationToken.None));
            Assert.Contains("002_orders.sql", problem.Message, StringComparison.Ordinal);
        }
        finally
        {
            await database.RunAsync("update schema_files set hash = substr(hash, 2) where name = '002_orders.sql'");
        }
    }
}
