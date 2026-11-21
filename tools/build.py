"""Build one verified MoviePilot V2/V3 release. Standard library only."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PID = "PtSubtitleFollowup"
PLUGIN = ROOT / "plugins.v2" / PID.lower()
V3_PLUGIN = ROOT / "plugins.v3" / PID.lower()
ENTRY = "dist/assets/remoteEntry.js"
QR = "assets/wechat-appreciation.jpg"
QR_SHA = "6d4dc8e46531420a80d547e932aad910d5b0daf33edb8f12093e41b8a58e0ae1"
FRONTEND = ("shell.js", "page.js", "shelf.js", "tonight.js", "subtitles.js", "support.js", "library-picker.js", "config.js", "entry.js")


def check(ok, message):
    if not ok:
        raise ValueError(message)


def source(path):
    check(not path.is_symlink() and path.is_file(), f"Missing or linked file: {path.name}")
    return path.read_text(encoding="utf-8").encode("utf-8")


def render_frontend():
    return b"// MoviePilot Viewing Assistant | Board-King\n" + b"\n".join(
        source(ROOT / "frontend" / part).rstrip() + b"\n" for part in FRONTEND
    )


def release_listing():
    listings = {}
    for generation, host in (("v2", "2.15.6"), ("v3", "3.2.9")):
        entries = json.loads(source(ROOT / f"package.{generation}.json"))
        check(set(entries) == {PID}, f"Unexpected {generation} plugin index")
        listing = entries[PID]
        check(listing.get("release") is True and listing.get("system_version") == f"=={host}",
              f"Review {generation} host compatibility before changing the release contract")
        if generation == "v2":
            check(listing.get("v3") is False, "V3 must use its own verified index")
        else:
            check(listing.get("v3") is not False and listing.get("v3t") is False,
                  "Review V3 runtime compatibility before changing the release contract")
        listings[generation] = listing
    compatibility_fields = {"system_version", "v3", "v3t"}
    shared = lambda listing: {key: value for key, value in listing.items() if key not in compatibility_fields}
    check(shared(listings["v2"]) == shared(listings["v3"]), "V2 and V3 must publish the same release metadata")
    return listings["v2"]


def sync_v3_mirror(files, write=False):
    """Keep the source-install fallback identical to the common release ZIP."""
    check(V3_PLUGIN.resolve() == V3_PLUGIN, "Linked V3 mirror directory")
    existing = {
        path.relative_to(V3_PLUGIN).as_posix()
        for path in V3_PLUGIN.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    check(not (existing - files.keys()), "Unexpected file in generated V3 mirror")
    for name, data in files.items():
        path = V3_PLUGIN / name
        check(path.resolve() == path, "Linked file in generated V3 mirror: " + name)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        check(path.is_file(), "Regenerate V3 source mirror with --write-v3: " + name)
        mirrored = path.read_bytes() if name == QR else source(path)
        check(mirrored == data, "Regenerate V3 source mirror with --write-v3: " + name)


def package(tag=None, write_v3=False):
    listing = release_listing()
    version = listing["version"]
    check(re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)",version), "Invalid version")
    expected_tag = f"{PID}_v{version}"
    check(not tag or tag == expected_tag, "Tag does not match version")
    check(listing.get("history", {}).get("v" + version), "Missing entry in version history")
    names = source(ROOT / "tools/package-files.txt").decode().splitlines()
    check(len(names) == len(set(names)) and ENTRY in names and QR in names, "Invalid package allowlist")
    py_files = {p.relative_to(PLUGIN).as_posix() for p in PLUGIN.rglob("*.py")}
    check(py_files == {name for name in names if name.endswith(".py")}, "Review Python file allowlist")
    files = {}
    for name in names:
        check(name in (QR, ENTRY, "NOTICE.txt") or re.fullmatch(r"(?:fusion/)?[a-z_]+\.py",name),"Invalid package path")
        path = PLUGIN / name
        check(path.resolve() == path and path.is_file(), "Missing file: "+name)
        data = path.read_bytes() if name == QR else source(path)
        if name.endswith(".py"):
            compile(data,name,"exec")
        files[name] = data
    check(files[ENTRY] == render_frontend(), "Regenerate the frontend with --write-ui")
    check(hashlib.sha256(files[QR]).hexdigest() == QR_SHA, "Unexpected appreciation image")
    tree = ast.parse(files["__init__.py"])
    plugin, = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == PID]
    values = {a.targets[0].id:a.value.value for a in plugin.body if isinstance(a,ast.Assign)
              and len(a.targets) == 1 and isinstance(a.targets[0],ast.Name) and isinstance(a.value,ast.Constant)}
    for key,field in (("plugin_name","name"),("plugin_version","version"),("plugin_desc","description"),
                      ("plugin_icon","icon"),("plugin_author","author")):
        check(values.get(key) == listing[field], f"Mismatch between {key} and index")
    check((ROOT / "icons/viewing-assistant.png").is_file(), "Public listing icon missing")
    sync_v3_mirror(files, write=write_v3)
    manifest_bytes = io.BytesIO()
    with zipfile.ZipFile(manifest_bytes,"w",compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            item = zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            item.create_system=3
            item.external_attr=0o100644 << 16
            archive.writestr(item,data)
    filename = expected_tag.lower()+".zip"
    result = manifest_bytes.getvalue()
    checksum = hashlib.sha256(result).hexdigest()
    return filename,result,f"{checksum}  {filename}\n".encode("ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag",help="Require this exact release tag")
    parser.add_argument("--write-ui",action="store_true",help="Rebuild the single Vue entry before packaging")
    parser.add_argument("--write-v3",action="store_true",help="Regenerate V3 fallback sources from the canonical V2 source")
    parser.add_argument("--verify",action="store_true",help="Check existing dist bytes instead of writing")
    args = parser.parse_args()
    check(not (args.verify and (args.write_ui or args.write_v3)), "Verification must not modify sources")
    if args.write_ui:
        (PLUGIN / ENTRY).write_bytes(render_frontend())
    filename, payload, sums = package(args.tag, write_v3=args.write_v3)
    out = ROOT / "dist"
    for name,data in ((filename,payload),("SHA256SUMS",sums)):
        if args.verify:
            check((out / name).read_bytes() == data, "Unexpected artifact: "+name)
        else:
            out.mkdir(exist_ok=True)
            (out / name).write_bytes(data)
    print(sums.decode().strip())
