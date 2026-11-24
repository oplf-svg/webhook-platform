# The API and the worker in one image. What it promises stands in docs/container.md.
#
# Nothing is run while it is built: no shell, no package manager, no network. So the files in it are the very files that the
# tests ran against, and for another kind of processor only the base is another one.
#
# .github/scripts/image.sh builds it. The folder that it sends to Docker holds api/, worker/, certificates/ and licenses/,
# nothing else.

# Microsoft's ASP.NET runtime without a shell. The fingerprint names a list that holds linux/amd64 and linux/arm64.
FROM mcr.microsoft.com/dotnet/aspnet:10.0.15-noble-chiseled@sha256:7c625e44e5d588b4d7b2a913e94ca4bd5098ce47442da404829e4c7936a764e1

# Not "/". The API looks for settings in its working folder, and would watch everything below it.
WORKDIR /app

COPY api/ api/
COPY worker/ worker/

# The roots of clouds that are in no public list, one file for each: they are not secret. Without them somebody would sooner
# or later switch off the check who the database is. image_look.py knows the fingerprint of each.
COPY certificates/ certificates/

# Our licence, whose it is, and the licence of each package that comes along.
COPY licenses/ licenses/

# Settings come from the environment alone, so no file is watched. No other process may look into this one, and with that
# nothing has to be written anywhere. The memory is collected as in the tests.
ENV ASPNETCORE_HTTP_PORTS=8080 \
    DOTNET_hostBuilder__reloadConfigOnChange=false \
    DOTNET_EnableDiagnostics=0 \
    DOTNET_gcServer=0

LABEL org.opencontainers.image.source="https://github.com/oplf-svg/webhook-platform" \
      org.opencontainers.image.title="webhook-platform" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="Takes signed webhooks and handles each one exactly once. The company is made up. The licence is that of its own files."

EXPOSE 8080
USER 1654:1654
STOPSIGNAL SIGTERM

# No HEALTHCHECK: the platforms ask over HTTP, and nothing in here could ask.
ENTRYPOINT ["/usr/bin/dotnet"]
CMD ["/app/api/Platform.Api.dll"]
