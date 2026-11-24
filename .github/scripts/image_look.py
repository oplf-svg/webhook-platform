"""Looks into the image before it is published. Nothing of it is run.

  image_look.py IMAGE.tar COMMIT             checks it, and prints the two fingerprints that the next jobs need
  image_look.py IMAGE.tar --contents FILE    checks a list of contents (SPDX) against the packages that are locked

What it does not check: whether the programs do what they should. That is what the tests are for.
"""

import contextlib
import gzip
import hashlib
import json
import pathlib
import re
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLATFORM = ("linux", "amd64")
TAG = "webhook-platform:new"
SOURCE = "https://github.com/oplf-svg/webhook-platform"
FROM = re.compile(r"FROM (mcr\.microsoft\.com)/(dotnet/aspnet):(10\.0\.\d+)-noble-chiseled@(sha256:[0-9a-f]{64})")
OURS = {"ASPNETCORE_HTTP_PORTS": "8080", "DOTNET_hostBuilder__reloadConfigOnChange": "false", "DOTNET_EnableDiagnostics": "0", "DOTNET_gcServer": "0"}
PROGRAMS = {"api": "Platform.Api", "worker": "Platform.Worker"}
# Whoever can change one of these can stand in for the database. Amazon's three roots for London are of May 2021 and last until
# 2061 and 2121. Read on 24 November 2026 from https://truststore.pki.rds.amazonaws.com/eu-west-2/eu-west-2-bundle.pem
CERTIFICATES = {"app/certificates/aws-rds-eu-west-2.pem": "sha256:17c557502061c4879b844fff983288a2fb07d520f4cf2a5de60f5cda800a4494"}
# The Apache License 2.0, letter by letter as on https://www.apache.org/licenses/LICENSE-2.0.txt
APACHE = "sha256:c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
HOME = "https://github.com/oplf-svg"
PLACES = {*PROGRAMS, "certificates", "licenses"}
# Nothing with which somebody could work in there. The last one has to be found, or nothing was looked at.
TOOLS = {"sh", "bash", "dash", "ash", "busybox", "apt", "apt-get", "dpkg", "curl", "wget", "perl", "python3", "openssl"}
# Text in a .NET file is UTF-16. The second word is always there, so it shows that such text is found at all.
MARKS = {"Platform.Core.dll": ("PLATFORM_FAULT", "order.placed"), "Platform.Data.dll": ("PLATFORM_SQL", "select pg_advisory_xact_lock")}
BASE_PACKAGES = {"base-files", "ca-certificates", "libc6", "libgcc-s1", "libssl3t64", "libstdc++6"}
# Not "openssl": the base names that package, but holds only its empty folders and not the program. TOOLS looks for the program.
TOOL_PACKAGES = {"apt", "bash", "busybox", "coreutils", "curl", "dash", "dpkg", "perl-base", "python3-minimal", "wget"}
KINDS = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"


class Refused(Exception):
    pass


def need(right, what):
    if not right:
        raise Refused(what)


def fingerprint(data):
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def get(address):
    """Reads from a registry that asks for no sign-in."""
    request = urllib.request.Request(address, headers={"Accept": KINDS})
    with urllib.request.urlopen(request, timeout=30) as answer:
        return answer.read(8 * 1024 * 1024)


def base_config(dockerfile, fetch=get):
    """What the base says about itself. Each step is checked against the fingerprint that the step before it named."""
    need(not re.search(r"(?im)^#\s*(syntax|escape|check)\s*=", dockerfile), "The Dockerfile asks for another reader.")
    lines = [line for line in dockerfile.splitlines() if line and not line.startswith("#")]
    found = FROM.fullmatch(lines[0]) if lines else None
    need(found, "The Dockerfile does not start from Microsoft's image at a fingerprint.")
    words = {line.split()[0] for line in re.sub(r"\\\n", " ", "\n".join(lines)).splitlines() if not line.startswith(" ")}
    need(words <= {"FROM", "WORKDIR", "COPY", "ENV", "LABEL", "EXPOSE", "USER", "STOPSIGNAL", "ENTRYPOINT", "CMD"}, f"The Dockerfile does more than copy: {sorted(words)}.")
    host, name, version, wanted = found.groups()

    def read(kind, digest):
        data = fetch(f"https://{host}/v2/{name}/{kind}/{digest}")
        need(fingerprint(data) == digest, f"The registry gave something else for {digest}.")
        return json.loads(data)

    ours = [one["digest"] for one in read("manifests", wanted)["manifests"] if (one["platform"]["os"], one["platform"]["architecture"]) == PLATFORM]
    need(len(ours) == 1, "The base is not there for this kind of processor, or twice.")
    config = read("blobs", read("manifests", ours[0])["config"]["digest"])
    need(f"DOTNET_VERSION={version}" in config["config"]["Env"], "The base holds another version than its name says.")
    return config


class Image:
    """A file that "docker save" has written."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.tar = tarfile.open(self.path)
        try:
            self.read_settings()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.tar.close()

    def read_settings(self):
        images = json.loads(self.read("manifest.json"))
        need(len(images) == 1 and images[0]["RepoTags"] == [TAG], "The file does not hold exactly the one image.")
        data = self.read(images[0]["Config"])
        self.id = fingerprint(data)
        need(images[0]["Config"].rpartition("/")[2].removesuffix(".json") == self.id[7:], "The settings are not the ones that the file names.")
        self.config = json.loads(data)
        self.layers = images[0]["Layers"]
        need(len(self.layers) == len(self.config["rootfs"]["diff_ids"]), "A layer is missing.")

    def read(self, name):
        return self.tar.extractfile(name).read()

    def layer(self, number):
        """The files of one layer, after its fingerprint was checked."""
        file = self.tar.extractfile(self.layers[number])
        if file.read(2) == b"\x1f\x8b":
            file.seek(0)
            file = gzip.GzipFile(fileobj=file)
        file.seek(0)
        hashed = hashlib.sha256()
        while part := file.read(1 << 20):
            hashed.update(part)
        need(f"sha256:{hashed.hexdigest()}" == self.config["rootfs"]["diff_ids"][number], f"Layer {number} is not the one that the settings name.")
        file.seek(0)
        return tarfile.open(fileobj=file, mode="r:")


def settings(lines):
    return dict(line.split("=", 1) for line in lines)


def check_settings(image, base, commit):
    config = image.config["config"]
    need((image.config["os"], image.config["architecture"]) == PLATFORM, "The image is for another kind of machine.")
    wanted = {
        "User": "1654:1654", "WorkingDir": "/app", "Entrypoint": ["/usr/bin/dotnet"], "Cmd": ["/app/api/Platform.Api.dll"],
        "ExposedPorts": {"8080/tcp": {}}, "StopSignal": "SIGTERM", "Volumes": None, "Healthcheck": None, "OnBuild": None,
    }
    for name, value in wanted.items():
        need((config.get(name) or None) == value, f"{name} is {config.get(name)!r} and not {value!r}.")
    need(settings(config["Env"]) == settings(base["config"]["Env"]) | OURS, "The settings inside are not those of the base and ours.")
    labels = config.get("Labels") or {}
    need(labels.get("org.opencontainers.image.revision") == commit, "The image does not name this commit.")
    need(labels.get("org.opencontainers.image.source") == SOURCE, "The image does not name this repo.")


def check_files(image, base):
    below = base["rootfs"]["diff_ids"]
    need(image.config["rootfs"]["diff_ids"][: len(below)] == below, "The image does not stand on the base that the Dockerfile names.")
    need(1 <= len(image.layers) - len(below) <= 5, "The image has another number of layers than the Dockerfile makes.")
    files, names = {}, set()
    for number in range(len(image.layers)):
        layer = image.layer(number)
        for member in layer:
            path = member.name.removeprefix("./").rstrip("/")
            # Not the folders: the base has an empty one for "dpkg", because a list of its packages lies there.
            if not member.isdir():
                names.add(path.rpartition("/")[2])
            if number < len(below):
                continue
            need(path == "app" or path.startswith("app/"), f"{path} is outside of /app.")
            need(".." not in path.split("/") and ".wh." not in path, f"{path} changes what is below.")
            need(path == "app" or path.split("/")[1] in PLACES and (path.count("/") > 1 or member.isdir()), f"{path} is in a place that nobody knows.")
            need(member.isfile() or member.isdir(), f"{path} is not a plain file.")
            need((member.uid, member.gid) == (0, 0), f"{path} does not belong to root.")
            need(not member.mode & 0o7022, f"{path} could be changed, or runs as somebody else.")
            need(member.mode & (0o555 if member.isdir() else 0o444) == (0o555 if member.isdir() else 0o444), f"{path} cannot be read by everybody.")
            if member.isfile():
                files[path] = layer.extractfile(member).read()
    need("dotnet" in names, "dotnet was not found, so the layers were not really looked into.")
    need(not names & TOOLS, f"There are tools inside: {sorted(names & TOOLS)}.")
    return files


def check_certificates(files):
    there = {path: data for path, data in files.items() if path.startswith("app/certificates/")}
    need(set(there) == set(CERTIFICATES), f"The certificates are not the ones that are known: {sorted(set(there) ^ set(CERTIFICATES))}.")
    for path, data in there.items():
        need(fingerprint(data) == CERTIFICATES[path], f"{path} is not the one that is known.")


def check_licences(files, labels, locks):
    """Our licence, whose it is, and the licence of every package that comes along: most of them ask for that."""
    there = {path.removeprefix("app/licenses/"): data for path, data in files.items() if path.startswith("app/licenses/")}
    wanted = {"LICENSE", "NOTICE"} | {f"{package.lower()}.txt" for packages in locks.values() for package, _ in packages}
    need(set(there) == wanted, f"The licences are not those of what is inside: {sorted(set(there) ^ wanted)}.")
    need(fingerprint(there["LICENSE"]) == APACHE, "LICENSE is not the Apache License 2.0, letter by letter.")
    lines = [line for line in there["NOTICE"].decode("utf-8").splitlines() if line.lower().startswith("copyright")]
    found = re.fullmatch(r"Copyright 20\d\d (\S.{0,60}\S)", lines[0]) if len(lines) == 1 else None
    need(found, "NOTICE does not say in one line whose it is.")
    need("OWNER_NAME" not in found.group(1), "The name in NOTICE is not filled in yet.")
    need(labels.get("org.opencontainers.image.authors") == f"{found.group(1)}, {HOME}", "The image names somebody else than NOTICE.")
    need(labels.get("org.opencontainers.image.licenses") == "Apache-2.0", "The image names another licence.")
    for name, data in there.items():
        need(len(data) > 200 or name == "NOTICE", f"{name} is too short to be a licence.")


def check_programs(files, locks):
    for path, data in files.items():
        need(not data.startswith((b"\x7fELF", b"#!", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")), f"{path} is a program for one kind of machine.")
    for part, name in PROGRAMS.items():
        need({f"app/{part}/{name}.dll", f"app/{part}/{name}.runtimeconfig.json", f"app/{part}/{name}.deps.json"} <= set(files), f"{part} is not complete.")
        frameworks = json.loads(files[f"app/{part}/{name}.runtimeconfig.json"])["runtimeOptions"]
        frameworks = frameworks.get("frameworks") or [frameworks["framework"]]
        need({"Microsoft.AspNetCore.App"} <= {one["name"] for one in frameworks} and all(one["version"].startswith("10.0.") for one in frameworks), f"{part} wants another runtime than the base has.")
        inside = {tuple(key.split("/")): value["sha512"] for key, value in json.loads(files[f"app/{part}/{name}.deps.json"])["libraries"].items() if value["type"] != "project"}
        need(inside == locks[part], f"The packages of {part} are not the locked ones: {sorted(inside)}.")
        for dll, (mark, always) in MARKS.items():
            data = files.get(f"app/{part}/{dll}", b"")
            need(always.encode("utf-16-le") in data, f"{dll} of {part} was not really looked into.")
            need(mark.encode("utf-16-le") not in data, f"{dll} of {part} is of the test build.")


def locked(root=ROOT):
    """The packages that each program may bring, from the lock files."""
    result = {}
    for part, name in PROGRAMS.items():
        groups = json.loads((root / "src" / name / "packages.lock.json").read_text(encoding="utf-8"))["dependencies"]
        need(list(groups) == ["net10.0"], f"The lock file of {part} has another form.")
        result[part] = {(package, value["resolved"]): f"sha512-{value['contentHash']}" for package, value in groups["net10.0"].items() if value["type"] != "Project"}
        need(result[part], f"The lock file of {part} names no package.")
    return result


def check_contents(contents, locks):
    """The list has to name what we know is inside. A tool that cannot read such an image would say nothing and be green."""
    need(contents.get("spdxVersion") == "SPDX-2.3", "The list has another form.")
    packages = [ref["referenceLocator"] for one in contents["packages"] for ref in one.get("externalRefs", []) if ref["referenceType"] == "purl"]
    debs = {found.group(1) for one in packages if (found := re.match(r"pkg:deb/ubuntu/([^@]+)@", one.replace("%2B", "+").replace("%2b", "+")))}
    nugets = {found.groups() for one in packages if (found := re.match(r"pkg:nuget/([^@]+)@([^?]+)", one))}
    need(BASE_PACKAGES <= debs, f"The list does not name what the base holds: {sorted(BASE_PACKAGES - debs)}.")
    need(not debs & TOOL_PACKAGES, f"The list names tools: {sorted(debs & TOOL_PACKAGES)}.")
    missing = {package for packages in locks.values() for package in packages} - nugets
    need(not missing, f"The list does not name {sorted(missing)}.")
    return len(debs), len(nugets)


def look(path, commit, base, locks):
    with contextlib.closing(Image(path)) as image:
        check_settings(image, base, commit)
        files = check_files(image, base)
        check_certificates(files)
        check_licences(files, image.config["config"].get("Labels") or {}, locks)
        check_programs(files, locks)
        return image, files


def main(arguments):
    try:
        if len(arguments) == 3 and arguments[1] == "--contents":
            debs, nugets = check_contents(json.loads(pathlib.Path(arguments[2]).read_text(encoding="utf-8")), locked())
            print(f"The list names {debs} packages of Ubuntu and {nugets} of .NET.", file=sys.stderr)
            return 0
        if len(arguments) != 2 or not re.fullmatch(r"[0-9a-f]{40}", arguments[1]):
            print(__doc__, file=sys.stderr)
            return 2
        base = base_config((ROOT / "Dockerfile").read_text(encoding="utf-8"))
        image, files = look(arguments[0], arguments[1], base, locked())
    except Refused as problem:
        print(f"Refused: {problem}", file=sys.stderr)
        return 1
    print(f"tar={hashlib.sha256(image.path.read_bytes()).hexdigest()}")
    print(f"config={image.id}")
    print(f"{len(files)} files of ours, {sum(map(len, files.values())):,} bytes, on {len(base['rootfs']['diff_ids'])} layers of the base.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
