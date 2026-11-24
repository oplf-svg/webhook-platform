using Platform.Core;
using Platform.Data;

namespace Platform.Api;

/// <summary>Takes one event: check it, store it, and only then say so.</summary>
sealed partial class Receiver(Feeds feeds, Inbox inbox, Refusals refusals, TimeProvider clock, ILogger<Receiver> log)
{
    public async Task<IResult> ReceiveAsync(string feed, HttpRequest request)
    {
        var envelope = Envelope.Read(request.Headers["webhook-id"], request.Headers["webhook-timestamp"], request.Headers["webhook-signature"]);
        if (envelope is null)
            return Refuse(StatusCodes.Status400BadRequest, "malformed");
        if (request.ContentLength > Limits.BodyBytes)
            return Refuse(StatusCodes.Status413PayloadTooLarge, "too large");
        if (!envelope.IsCurrent(clock.GetUtcNow()))
            return Refuse(StatusCodes.Status401Unauthorized, "refused", "wrong time");

        byte[] body;
        try
        {
            using var buffer = new MemoryStream();
            await request.Body.CopyToAsync(buffer, request.HttpContext.RequestAborted);
            body = buffer.ToArray();
        }
        catch (BadHttpRequestException problem)
        {
            return Refuse(problem.StatusCode, problem.StatusCode == StatusCodes.Status413PayloadTooLarge ? "too large" : "malformed");
        }

        // Once more, because a body can take its time.
        if (!envelope.IsCurrent(clock.GetUtcNow()))
            return Refuse(StatusCodes.Status401Unauthorized, "refused", "wrong time");

        // The same work for a feed that does not exist, so that nobody learns which ones do.
        var known = feeds.Find(feed);
        if (!Signature.IsRight(known.Secrets, envelope, body))
            return Refuse(StatusCodes.Status401Unauthorized, "refused", "wrong signature");

        // From here on it is a real event. It is stored even if whoever sent it has gone by now.
        Fault.At("api.after_verify");
        Stored stored;
        try
        {
            stored = await inbox.StoreAsync(known.Name, envelope.Id, known.Shop, body);
        }
        catch (Exception problem) when (Trouble.Is(problem))
        {
            NotStored(log, known.Name, envelope.Id, problem);
            request.HttpContext.Response.Headers.RetryAfter = "2";
            return Answer(StatusCodes.Status503ServiceUnavailable, "not stored");
        }

        Fault.At("api.after_store");
        // Another body under a used id is a mistake of the feed, or somebody else has its secret.
        Took(log, stored == Stored.Conflict ? LogLevel.Warning : LogLevel.Information, known.Name, envelope.Id, stored);
        return stored switch
        {
            Stored.New => Answer(StatusCodes.Status202Accepted, "stored"),
            Stored.Already => Answer(StatusCodes.Status200OK, "already here"),
            _ => Answer(StatusCodes.Status409Conflict, "another body is here under this id"),
        };

        IResult Refuse(int status, string result, string? reason = null)
        {
            refusals.Add(feed, reason ?? result);
            return Answer(status, result);
        }
    }

    static IResult Answer(int status, string result) => Results.Json(new { result }, statusCode: status);

    [LoggerMessage("{Feed} {Event}: {Stored}")]
    static partial void Took(ILogger log, LogLevel level, string feed, string @event, Stored stored);

    [LoggerMessage(LogLevel.Error, "{Feed} {Event}: not stored")]
    static partial void NotStored(ILogger log, string feed, string @event, Exception problem);
}
