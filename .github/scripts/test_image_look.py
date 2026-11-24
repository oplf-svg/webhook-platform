"""Shows that image_look.py refuses. Each mistake is made alone, in an image that is right otherwise."""

import copy
import hashlib
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import image_look as look

COMMIT = "a" * 40
HASH = "sha512-" + "x" * 86 + "=="
CERTIFICATE = b"-----BEGIN CERTIFICATE-----"
LICENCE = b"Apache License" * 20
LOCKS = {part: {("Npgsql", "10.0.3"): HASH} for part in look.PROGRAMS}
ENV = ["PATH=/usr/bin", "APP_UID=1654", "ASPNETCORE_HTTP_PORTS=8080", "DOTNET_VERSION=10.0.15"]


def fingerprint(data):
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def text(*words):
    return b"MZ" + b"".join(word.encode("utf-16-le") for word in words)


def layer(*members):
    """Each member: name, bytes or None for a folder, and what is different about it."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data, *other in members:
            one = tarfile.TarInfo(name)
            one.type, one.mode = (tarfile.DIRTYPE, 0o755) if data is None else (tarfile.REGTYPE, 0o644)
            one.size = len(data or b"")
            for key, value in (other[0] if other else {}).items():
                setattr(one, key, value)
            tar.addfile(one, io.BytesIO(data or b""))
    return buffer.getvalue()


def program(name, core=("order.placed",), data=("select pg_advisory_xact_lock",), packages=None, frameworks=("Microsoft.NETCore.App", "Microsoft.AspNetCore.App"), version="10.0.0"):
    libraries = {f"{name}/1.0.0": {"type": "project", "sha512": ""}}
    libraries |= {f"{package}/{number}": {"type": "package", "sha512": value} for (package, number), value in (packages or {("Npgsql", "10.0.3"): HASH}).items()}
    return {
        f"{name}.dll": text("PLATFORM_DATABASE"),
        f"{name}.runtimeconfig.json": json.dumps({"runtimeOptions": {"frameworks": [{"name": one, "version": version} for one in frameworks]}}).encode(),
        f"{name}.deps.json": json.dumps({"libraries": libraries}).encode(),
        "Platform.Core.dll": text(*core), "Platform.Data.dll": text(*data), "Npgsql.dll": text(),
    }


class Looked(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = pathlib.Path(folder.name) / "image.tar"
        self.below = [layer(("usr/", None), ("usr/bin/", None), ("usr/bin/dotnet", b"dotnet")), layer(("etc/", None), ("etc/passwd", b"app"), ("var/lib/dpkg/", None), ("var/lib/dpkg/status", b"x"))]
        self.base = {"os": "linux", "architecture": "amd64", "config": {"Env": list(ENV)}, "rootfs": {"diff_ids": [fingerprint(one) for one in self.below]}}
        self.programs = {"api": program("Platform.Api"), "worker": program("Platform.Worker")}
        self.certificates = {"aws-rds-eu-west-2.pem": CERTIFICATE}
        known = look.CERTIFICATES
        look.CERTIFICATES = {"app/certificates/aws-rds-eu-west-2.pem": fingerprint(CERTIFICATE)}
        self.addCleanup(setattr, look, "CERTIFICATES", known)
        self.licences = {"LICENSE": LICENCE, "NOTICE": b"webhook-platform\nCopyright 2026 A Name\n", "npgsql.txt": b"Npgsql" * 50}
        apache = look.APACHE
        look.APACHE = fingerprint(LICENCE)
        self.addCleanup(setattr, look, "APACHE", apache)
        self.more = []
        self.config = {
            "os": "linux", "architecture": "amd64",
            "config": {
                "User": "1654:1654", "WorkingDir": "/app", "Entrypoint": ["/usr/bin/dotnet"], "Cmd": ["/app/api/Platform.Api.dll"],
                "ExposedPorts": {"8080/tcp": {}}, "StopSignal": "SIGTERM", "Env": ENV + [f"{key}={value}" for key, value in look.OURS.items() if key != "ASPNETCORE_HTTP_PORTS"],
                "Labels": {"org.opencontainers.image.revision": COMMIT, "org.opencontainers.image.source": look.SOURCE, "org.opencontainers.image.authors": f"A Name, {look.HOME}", "org.opencontainers.image.licenses": "Apache-2.0"},
            },
        }

    def own(self):
        made = [layer(("app/", None))]
        for part, files in self.programs.items():
            made.append(layer(("app/", None), (f"app/{part}/", None), *((f"app/{part}/{name}", data) for name, data in files.items())))
        made.append(layer(("app/", None), ("app/certificates/", None), *((f"app/certificates/{name}", data) for name, data in self.certificates.items())))
        made.append(layer(("app/", None), ("app/licenses/", None), *((f"app/licenses/{name}", data) for name, data in self.licences.items())))
        return made + self.more

    def write(self, change=None, rename=None, spoil=None):
        layers = self.below + self.own()
        config = copy.deepcopy(self.config)
        config["rootfs"] = {"type": "layers", "diff_ids": [fingerprint(one) for one in layers]}
        data = json.dumps(config).encode()
        names = [f"blobs/sha256/{fingerprint(one)[7:]}" for one in layers]
        images = [{"Config": f"blobs/sha256/{fingerprint(data)[7:]}", "RepoTags": [look.TAG], "Layers": names}]
        if change:
            change(images)
        with tarfile.open(self.path, "w") as tar:
            for name, content in [("manifest.json", json.dumps(images).encode()), (rename or images[0]["Config"], data), *zip(names, layers)]:
                if spoil == name or (spoil == "layer" and name == names[-1]):
                    content = layer(("app/", None), ("app/api/", None), ("app/api/other.dll", text("other")))
                one = tarfile.TarInfo(name)
                one.size = len(content)
                tar.addfile(one, io.BytesIO(content))

    def look(self, **how):
        self.write(**how)
        return look.look(self.path, COMMIT, self.base, LOCKS)

    def refused(self, words, **how):
        with self.assertRaises(look.Refused) as caught:
            self.look(**how)
        self.assertIn(words, str(caught.exception))

    # What is right

    def test_a_right_image_passes(self):
        image, files = self.look()
        self.assertEqual(len(files), 16)
        self.assertRegex(image.id, "^sha256:[0-9a-f]{64}$")

    def test_compressed_layers_pass_too(self):
        import gzip
        plain = self.own

        def packed():
            return plain()

        self.write()
        # The newer store of Docker writes layers compressed. The fingerprints are of the plain form.
        layers = self.below + packed()
        with tarfile.open(self.path) as tar:
            images = json.loads(tar.extractfile("manifest.json").read())
            config = tar.extractfile(images[0]["Config"]).read()
        with tarfile.open(self.path, "w") as tar:
            for name, content in [("manifest.json", json.dumps(images).encode()), (images[0]["Config"], config), *zip(images[0]["Layers"], map(gzip.compress, layers))]:
                one = tarfile.TarInfo(name)
                one.size = len(content)
                tar.addfile(one, io.BytesIO(content))
        look.look(self.path, COMMIT, self.base, LOCKS)

    # The file

    def test_two_images(self):
        self.refused("exactly the one image", change=lambda images: images.append(images[0]))

    def test_another_tag(self):
        self.refused("exactly the one image", change=lambda images: images[0].update(RepoTags=["ghcr.io/oplf-svg/webhook-platform:latest"]))

    def test_one_more_tag(self):
        self.refused("exactly the one image", change=lambda images: images[0]["RepoTags"].append("postgres:18.7"))

    def test_settings_of_another_name(self):
        wrong = "blobs/sha256/" + "0" * 64
        self.refused("not the ones that the file names", change=lambda images: images[0].update(Config=wrong), rename=wrong)

    def test_a_layer_left_out(self):
        self.refused("A layer is missing", change=lambda images: images[0]["Layers"].pop())

    def test_a_layer_that_is_another_one(self):
        self.refused("is not the one that the settings name", spoil="layer")

    # The settings

    def test_settings(self):
        mistakes = [
            ("User", "root"), ("User", ""), ("User", "1654"), ("User", "0:0"), ("WorkingDir", "/"), ("Entrypoint", ["/bin/sh", "-c"]), ("Entrypoint", None),
            ("Cmd", ["/app/worker/Platform.Worker.dll"]), ("ExposedPorts", {"8080/tcp": {}, "22/tcp": {}}), ("ExposedPorts", None), ("StopSignal", "SIGKILL"), ("StopSignal", None),
            ("Volumes", {"/app": {}}), ("Healthcheck", {"Test": ["CMD", "x"]}), ("OnBuild", ["RUN x"]),
        ]
        for name, value in mistakes:
            with self.subTest(name=name, value=value):
                right = copy.deepcopy(self.config)
                self.config["config"][name] = value
                self.refused(name)
                self.config = right

    def test_environment(self):
        for change in (lambda env: env.append("PLATFORM_DATABASE=Password=x"), lambda env: env.remove("DOTNET_EnableDiagnostics=0"), lambda env: env.__setitem__(0, "PATH=/app"), lambda env: env.append("DOTNET_STARTUP_HOOKS=/app/x.dll")):
            with self.subTest():
                right = copy.deepcopy(self.config)
                change(self.config["config"]["Env"])
                self.refused("not those of the base and ours")
                self.config = right

    def test_labels(self):
        for name, value, words in (("revision", "b" * 40, "this commit"), ("revision", "unknown", "this commit"), ("source", "https://github.com/somebody/else", "this repo"), ("licenses", "MIT", "another licence"),
                                   ("licenses", "", "another licence"), ("authors", f"Another Name, {look.HOME}", "somebody else"), ("authors", "A Name", "somebody else"), ("authors", "", "somebody else")):
            with self.subTest(name):
                right = copy.deepcopy(self.config)
                self.config["config"]["Labels"][f"org.opencontainers.image.{name}"] = value
                self.refused(words)
                self.config = right

    def test_another_machine(self):
        self.config["architecture"] = "arm64"
        self.refused("another kind of machine")

    # The layers and the files

    def test_another_base(self):
        self.base["rootfs"]["diff_ids"][1] = "sha256:" + "1" * 64
        self.refused("does not stand on the base")

    def test_a_base_with_more(self):
        self.base["rootfs"]["diff_ids"].append("sha256:" + "1" * 64)
        self.refused("does not stand on the base")

    def test_too_many_layers(self):
        self.more = [layer(("app/", None))]
        self.refused("another number of layers")

    def test_nothing_of_ours(self):
        self.own = lambda: []
        self.refused("another number of layers")

    def test_files(self):
        mistakes = [
            (("etc/passwd", b"root"), "outside of /app"), (("app/../etc/passwd", b"root"), "changes what is below"), (("app/.wh.api", b""), "changes what is below"),
            (("app/worker/.wh..wh..opq", b""), "changes what is below"), (("app/worker/link", b"", {"type": tarfile.SYMTYPE, "linkname": "/etc/passwd"}), "not a plain file"),
            (("app/worker/hard", b"", {"type": tarfile.LNKTYPE, "linkname": "app/worker/Npgsql.dll"}), "not a plain file"), (("app/worker/null", b"", {"type": tarfile.CHRTYPE}), "not a plain file"),
            (("app/worker/x", b"x", {"uid": 1654}), "does not belong to root"), (("app/worker/x", b"x", {"gid": 1654}), "does not belong to root"),
            (("app/worker/x", b"x", {"mode": 0o4755}), "could be changed"), (("app/worker/x", b"x", {"mode": 0o666}), "could be changed"), (("app/worker/x", b"x", {"mode": 0o664}), "could be changed"),
            (("app/worker/", None, {"mode": 0o777}), "could be changed"), (("app/worker/x", b"x", {"mode": 0o600}), "cannot be read"), (("app/worker/", None, {"mode": 0o700}), "cannot be read"),
            (("app/worker/sh", b"x"), "tools inside"), (("app/worker/busybox", b"x"), "tools inside"),
            (("app/worker/Platform.Worker", b"\x7fELFx"), "one kind of machine"), (("app/worker/run", b"#!/bin/sh"), "one kind of machine"),
            (("app/other/x.dll", b"x"), "that nobody knows"), (("app/appsettings.json", b"{}"), "that nobody knows"), (("app/api", b"x"), "that nobody knows"), (("app/other/", None), "that nobody knows"),
        ]
        plain = self.own
        for member, words in mistakes:
            with self.subTest(member[0], words=words):
                self.own = lambda member=member: [*plain()[:2], layer(*((f"app/worker/{name}", data) for name, data in self.programs["worker"].items()), member), *plain()[3:]]
                self.refused(words)
        self.own = plain

    def test_a_tool_in_the_base(self):
        for member in (("usr/bin/bash", b"x"), ("usr/bin/dpkg", b"x"), ("bin/sh", b"", {"type": tarfile.SYMTYPE, "linkname": "dash"}), ("usr/local/bin/busybox", b"", {"type": tarfile.LNKTYPE, "linkname": "usr/bin/dotnet"})):
            with self.subTest(member[0]):
                self.below[1] = layer(member)
                self.base["rootfs"]["diff_ids"][1] = fingerprint(self.below[1])
                self.refused("tools inside")

    def test_nothing_was_looked_into(self):
        self.below[0] = layer(("usr/", None))
        self.base["rootfs"]["diff_ids"][0] = fingerprint(self.below[0])
        self.refused("not really looked into")

    # The certificates

    def test_certificates(self):
        mistakes = [
            ({}, "not the ones that are known"), ({"aws-rds-eu-west-2.pem": CERTIFICATE + b" "}, "is not the one that is known"), ({"aws-rds-eu-west-2.pem": b""}, "is not the one that is known"),
            ({"aws-rds-eu-west-2.pem": CERTIFICATE, "another.pem": CERTIFICATE}, "not the ones that are known"), ({"another.pem": CERTIFICATE}, "not the ones that are known"),
            ({"aws-rds-eu-west-2.pem": CERTIFICATE + b"and something else"}, "is not the one that is known"),
        ]
        for certificates, words in mistakes:
            with self.subTest(certificates):
                self.certificates = certificates
                self.refused(words)

    def test_the_known_fingerprint_has_the_right_form(self):
        self.doCleanups()
        self.assertTrue(look.CERTIFICATES)
        for path, known in look.CERTIFICATES.items():
            self.assertRegex(path, r"^app/certificates/[a-z0-9-]+\.pem$")
            self.assertRegex(known, r"^sha256:[0-9a-f]{64}$")
            file = look.ROOT / path.removeprefix("app/")
            if file.exists():
                self.assertEqual(fingerprint(file.read_bytes()), known)

    # The licences

    def test_licences(self):
        notice = "webhook-platform\n{}\n"
        mistakes = [
            ({"npgsql.txt": None}, "not those of what is inside"), ({"LICENSE": None}, "not those of what is inside"), ({"NOTICE": None}, "not those of what is inside"), ({"other.txt": b"x" * 300}, "not those of what is inside"),
            ({"LICENSE": LICENCE + b" "}, "letter by letter"), ({"npgsql.txt": b"short"}, "too short"),
            ({"NOTICE": notice.format("Copyright 2026 OWNER_NAME").encode()}, "not filled in"), ({"NOTICE": notice.format("").encode()}, "whose it is"), ({"NOTICE": notice.format("Copyright 2026 ").encode()}, "whose it is"),
            ({"NOTICE": notice.format("Copyright A Name").encode()}, "whose it is"), ({"NOTICE": notice.format("Copyright 2026 A Name\nCopyright 2026 Another").encode()}, "whose it is"),
            ({"NOTICE": notice.format("Copyright 2026 Another Name").encode()}, "somebody else"), ({"NOTICE": notice.format("Copyright 2026 " + "x" * 80).encode()}, "whose it is"),
        ]
        for change, words in mistakes:
            with self.subTest(change):
                right = dict(self.licences)
                self.licences = {name: data for name, data in (self.licences | change).items() if data is not None}
                self.refused(words)
                self.licences = right

    def test_a_new_package_needs_its_licence(self):
        other = {("Npgsql", "10.0.3"): HASH, ("Another.Package", "1.0.0"): HASH}
        self.write()
        with self.assertRaisesRegex(look.Refused, "another.package.txt"):
            look.look(self.path, COMMIT, self.base, {part: other for part in look.PROGRAMS})

    def test_the_licence_of_the_repo(self):
        self.doCleanups()
        file = look.ROOT / "LICENSE"
        if file.exists():
            self.assertEqual(fingerprint(file.read_bytes()), look.APACHE)

    # The programs

    def test_programs(self):
        other = {("Npgsql", "10.0.3"): HASH, ("Newtonsoft.Json", "13.0.3"): HASH}
        mistakes = [
            (dict(core=("order.placed", "PLATFORM_FAULT")), "of the test build"), (dict(data=("select pg_advisory_xact_lock", "PLATFORM_SQL")), "of the test build"),
            (dict(core=()), "not really looked into"), (dict(data=()), "not really looked into"), (dict(packages=other), "not the locked ones"),
            (dict(packages={("Npgsql", "10.0.2"): HASH}), "not the locked ones"), (dict(packages={("Npgsql", "10.0.3"): "sha512-other"}), "not the locked ones"),
            (dict(frameworks=("Microsoft.NETCore.App",)), "another runtime"), (dict(version="11.0.0"), "another runtime"),
        ]
        for part, name in look.PROGRAMS.items():
            for how, words in mistakes:
                with self.subTest(part=part, how=how):
                    self.programs[part] = program(name, **how)
                    self.refused(words)
                    self.programs[part] = program(name)

    def test_a_program_that_is_not_complete(self):
        for part, name in look.PROGRAMS.items():
            for end in (".dll", ".deps.json", ".runtimeconfig.json"):
                with self.subTest(part=part, end=end):
                    del self.programs[part][name + end]
                    self.refused("is not complete")
                    self.programs[part] = program(name)


class Base(unittest.TestCase):
    def setUp(self):
        self.config = json.dumps({"os": "linux", "architecture": "amd64", "config": {"Env": ENV}, "rootfs": {"diff_ids": []}}).encode()
        self.manifest = json.dumps({"config": {"digest": fingerprint(self.config)}}).encode()
        self.platforms = [("linux", "amd64", self.manifest), ("linux", "arm64", b"{}")]
        self.build()

    def build(self):
        self.index = json.dumps({"manifests": [{"digest": fingerprint(data), "platform": {"os": os, "architecture": kind}} for os, kind, data in self.platforms]}).encode()
        self.there = {fingerprint(one): one for one in (self.config, self.manifest, self.index)}
        self.file = f"# a word\nFROM mcr.microsoft.com/dotnet/aspnet:10.0.15-noble-chiseled@{fingerprint(self.index)}\nWORKDIR /app\nCOPY api/ api/\nENV A=1 \\\n    B=2\nCMD [\"x\"]\n"

    def read(self, text=None):
        asked = []

        def fetch(address):
            asked.append(address)
            return self.there[address.rpartition("/")[2]]

        config = look.base_config(text or self.file, fetch)
        self.assertTrue(all(address.startswith("https://mcr.microsoft.com/v2/dotnet/aspnet/") for address in asked))
        return config

    def test_right(self):
        self.assertEqual(self.read()["config"]["Env"], ENV)

    def test_the_real_dockerfile_has_the_right_form(self):
        file = look.ROOT / "Dockerfile"
        with self.assertRaises(KeyError):
            self.read(file.read_text())

    def test_other_bases(self):
        for old, new in (("@" + fingerprint(self.index), ""), ("mcr.microsoft.com", "mcr.example.com"), ("dotnet/aspnet", "dotnet/sdk"), ("-noble-chiseled", "-noble"), ("10.0.15", "10.0"), ("FROM ", "FROM --platform=linux/arm64 ")):
            with self.subTest(old), self.assertRaisesRegex(look.Refused, "does not start from"):
                self.read(self.file.replace(old, new))

    def test_more_than_copying(self):
        for line in ("RUN echo x", "ADD https://example.com/x /x", "FROM scratch AS other\nRUN x", "VOLUME /x", "HEALTHCHECK NONE", "ONBUILD RUN x", "SHELL [\"x\"]", "ARG X"):
            with self.subTest(line), self.assertRaisesRegex(look.Refused, "does more than copy"):
                self.read(self.file + line + "\n")

    def test_another_reader(self):
        for line in ("# syntax=docker/dockerfile:1", "#syntax = x", "# escape=`", "# check=skip=all"):
            with self.subTest(line), self.assertRaisesRegex(look.Refused, "another reader"):
                self.read(line + "\n" + self.file)

    def test_the_registry_gives_something_else(self):
        for one in list(self.there):
            with self.subTest(one):
                right = self.there[one]
                self.there[one] = right + b" "
                with self.assertRaisesRegex(look.Refused, "gave something else"):
                    self.read()
                self.there[one] = right

    def test_not_for_this_machine(self):
        for platforms in ([("linux", "arm64", self.manifest)], [("linux", "amd64", self.manifest)] * 2, [("windows", "amd64", self.manifest)]):
            self.platforms = platforms
            self.build()
            with self.subTest(platforms=len(platforms)), self.assertRaisesRegex(look.Refused, "not there for this kind"):
                self.read()

    def test_another_version_inside(self):
        with self.assertRaisesRegex(look.Refused, "another version"):
            self.read(self.file.replace("10.0.15", "10.0.16"))


class Contents(unittest.TestCase):
    def setUp(self):
        self.names = [f"pkg:deb/ubuntu/{one.replace('+', '%2B')}@1?arch=amd64" for one in look.BASE_PACKAGES] + ["pkg:nuget/Npgsql@10.0.3", "pkg:nuget/Microsoft.NETCore.App.Runtime.linux-x64@10.0.15"]

    def check(self):
        return look.check_contents({"spdxVersion": "SPDX-2.3", "packages": [{"externalRefs": [{"referenceType": "purl", "referenceLocator": one}]} for one in self.names] + [{"name": "without"}]}, LOCKS)

    def test_right(self):
        self.assertEqual(self.check(), (len(look.BASE_PACKAGES), 2))

    def test_what_is_missing(self):
        for number in range(len(look.BASE_PACKAGES) + 1):
            with self.subTest(self.names[number]):
                taken = self.names.pop(number)
                with self.assertRaisesRegex(look.Refused, "does not name"):
                    self.check()
                self.names.insert(number, taken)

    def test_another_version(self):
        self.names[-2] = "pkg:nuget/Npgsql@10.0.2"
        with self.assertRaisesRegex(look.Refused, "does not name"):
            self.check()

    def test_an_empty_list(self):
        self.names = []
        with self.assertRaisesRegex(look.Refused, "does not name"):
            self.check()

    def test_a_tool(self):
        self.names.append("pkg:deb/ubuntu/bash@5")
        with self.assertRaisesRegex(look.Refused, "names tools"):
            self.check()

    def test_another_form(self):
        with self.assertRaisesRegex(look.Refused, "another form"):
            look.check_contents({"packages": []}, LOCKS)


class Locked(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = pathlib.Path(folder.name)

    def read(self, groups):
        for name in look.PROGRAMS.values():
            place = self.root / "src" / name
            place.mkdir(parents=True, exist_ok=True)
            (place / "packages.lock.json").write_text(json.dumps({"version": 2, "dependencies": groups}))
        return look.locked(self.root)

    def test_right(self):
        found = self.read({"net10.0": {"platform.core": {"type": "Project"}, "Npgsql": {"type": "CentralTransitive", "resolved": "10.0.3", "contentHash": HASH[7:]}}})
        self.assertEqual(found, LOCKS)

    def test_another_framework(self):
        with self.assertRaisesRegex(look.Refused, "another form"):
            self.read({"net10.0": {}, "net11.0": {}})

    def test_nothing_locked(self):
        with self.assertRaisesRegex(look.Refused, "names no package"):
            self.read({"net10.0": {"platform.core": {"type": "Project"}}})


if __name__ == "__main__":
    unittest.main()
