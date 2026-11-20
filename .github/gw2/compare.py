#!/usr/bin/env python3
# gw2 (jdrly/gw2-on-mac#339): compare my build of KosmicKrisp with the one in Mach9's engine, and
# this commit's objects with my build of the engine's commit. It reads files and runs nothing.
#
#   compare.py gate <mine.dylib> <engine.dylib> <mine.json> <engine.json>
#   compare.py record <record dir> <source dir> <build dir>
#   compare.py patched <base record dir> <head record dir> [<changed path>...]
#   compare.py flags <build dir> <source path in the tree>
#   compare.py selftest <engine.dylib> <engine.json>
#
# Each prints a Markdown report that ends in "failed: ..." or "all checks passed" and exits 1
# when a check fails.
#
# gate: the structural gate, as my FEX and Wine forks use it for a build with another Xcode than
#   the engine's, whose clang can't give the engine's bytes. Against the engine's file: a dylib
#   with its install name, rpaths and libraries, minos, exports, imports (each symbol from the
#   same library), segments by name, order and kind, sections by name and kind, and the same
#   functions by name. The names the compiler makes up (_OUTLINED_FUNCTION_<n>, <name>.cold.<n>)
#   are left out and only counted: another clang numbers them its own way. Which named functions
#   differ in size is a report and fails nothing. Then the fix's own check: no entry of
#   __DATA,__objc_selrefs names sparseBufferTier, so nothing in the file can send it. And the ICD
#   manifest is the engine's.
# record: after a build, from the files mesa.zsh record wrote (ninja -t inputs of the dylib,
#   ninja -t deps, ninja -t compdb): each object the dylib links, its sha256, its compile command
#   and its inputs (its source and every header the compiler read), and the sha256 of every input
#   that is a file of the build tree (generated headers).
# patched: this commit's build, made in the same trees after the base build with every object
#   compiled again. Each object none of whose inputs the commit changes (a <changed path> from git
#   diff, or a generated input whose hash differs) and whose compile command is the base's has to
#   be the base build's byte for byte. The others are listed with what touched them.
# flags: the compile command of one source in the build, as NUL-separated arguments without its
#   source, output and dependency file, for the native test (mesa.zsh test).
# selftest: the gate on the engine's own file and on copies of it changed by hand. The file
#   against itself passes except the selector check (the engine's file sends sparseBufferTier),
#   and each change fails.
import hashlib
import json
import os
import re
import shlex
import struct
import sys
import tempfile

ARTIFACT = re.compile(r"^_OUTLINED_FUNCTION_\d+$|\.cold\.\d+$")
SELECTOR = "sparseBufferTier"
# What the fix takes out of the engine's file, and nothing else may differ: the selector, and the
# import clang calls for respondsToSelector:, whose only use in the driver was the same line (my
# build may still import it if anything else calls it, which the report says)
FIX_IMPORTS = {"objc_opt_respondsToSelector"}
DYLIB = "src/kosmickrisp/vulkan/libvulkan_kosmickrisp.dylib"
# the object the fix changes, which every record has to have, so an empty or wrong list of
# objects can't pass
FIX_OBJECT = "src/kosmickrisp/bridge/libmtl_bridge.a.p/mtl_buffer.m.o"


class Report:
    def __init__(self):
        self.failed = 0

    def ok(self, text):
        print(f"- ok: {text}")

    def fail(self, text):
        print(f"- **FAIL**: {text}")
        self.failed += 1

    def check(self, cond, good, bad):
        self.ok(good) if cond else self.fail(bad)


def listing(names, limit=40):
    names = sorted(names)
    shown = ", ".join(f"`{n}`" for n in names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def cstr(data, off):
    end = data.index(b"\0", off)
    return data[off:end].decode("utf-8", "replace")


# Mach-O, as my FEX fork's compare.py reads it

LC_REQ_DYLD = 0x80000000
LC_SEGMENT_64, LC_SYMTAB = 0x19, 0x2
LC_LOAD_DYLIB, LC_ID_DYLIB, LC_LOAD_WEAK_DYLIB = 0xc, 0xd, 0x18 | LC_REQ_DYLD
LC_REEXPORT_DYLIB, LC_LAZY_LOAD_DYLIB, LC_LOAD_UPWARD_DYLIB = 0x1f | LC_REQ_DYLD, 0x20, 0x23 | LC_REQ_DYLD
LC_RPATH, LC_BUILD_VERSION = 0x1c | LC_REQ_DYLD, 0x32
DYLIB_CMDS = {LC_LOAD_DYLIB: "", LC_LOAD_WEAK_DYLIB: " (weak)", LC_REEXPORT_DYLIB: " (reexport)",
              LC_LAZY_LOAD_DYLIB: " (lazy)", LC_LOAD_UPWARD_DYLIB: " (upward)"}
N_STAB, N_TYPE, N_EXT, N_UNDF, N_SECT = 0xe0, 0x0e, 0x01, 0x0, 0xe


def version(v):
    return f"{v >> 16}.{(v >> 8) & 0xff}" + (f".{v & 0xff}" if v & 0xff else "")


class MachO:
    def __init__(self, path, data=None):
        self.path = path
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        self.data = d = data
        magic, cputype, _, filetype, ncmds, _, _, _ = struct.unpack_from("<IiiIIIII", d, 0)
        if magic != 0xfeedfacf or cputype != 0x0100000c:
            raise SystemExit(f"{path}: not an arm64 Mach-O file")
        self.filetype = filetype
        self.sections, self.segments, self.commands, self.dylibs, self.rpaths = [], [], [], [], []
        self.install_name = self.minos = self.sdk = None
        symtab = None
        off = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", d, off)
            self.commands.append((cmd, off, size))
            if cmd == LC_SEGMENT_64:
                segname = d[off + 8:off + 24].rstrip(b"\0").decode()
                _, _, _, _, maxprot, initprot, nsects = struct.unpack_from("<QQQQiiI", d, off + 24)
                self.segments.append(dict(name=segname, maxprot=maxprot, initprot=initprot))
                for i in range(nsects):
                    s = off + 72 + i * 80
                    sect, seg = d[s:s + 16].rstrip(b"\0").decode(), d[s + 16:s + 32].rstrip(b"\0").decode()
                    addr, sz, foff, _, _, _, flags, _, _ = struct.unpack_from("<QQIIIIIII", d, s + 32)
                    self.sections.append(dict(name=f"{seg},{sect}", addr=addr, size=sz, offset=foff,
                                              flags=flags, type=flags & 0xff))
            elif cmd in DYLIB_CMDS or cmd == LC_ID_DYLIB:
                name = cstr(d, off + struct.unpack_from("<I", d, off + 8)[0])
                if cmd == LC_ID_DYLIB:
                    self.install_name = name
                else:
                    self.dylibs.append(name + DYLIB_CMDS[cmd])
            elif cmd == LC_RPATH:
                self.rpaths.append(cstr(d, off + struct.unpack_from("<I", d, off + 8)[0]))
            elif cmd == LC_BUILD_VERSION:
                _, minos, sdk = struct.unpack_from("<III", d, off + 8)
                self.minos, self.sdk = version(minos), version(sdk)
            elif cmd == LC_SYMTAB:
                symtab = struct.unpack_from("<IIII", d, off + 8)
            off += size
        if not symtab:
            raise SystemExit(f"{path}: no symbol table")
        symoff, nsyms, stroff, _ = symtab
        self.symoff = symoff
        self.syms = []
        for i in range(nsyms):
            strx, ntype, nsect, ndesc, value = struct.unpack_from("<IBBHQ", d, symoff + i * 16)
            self.syms.append((cstr(d, stroff + strx), ntype, nsect, ndesc, value))
        self.exports = sorted(n[1:] for n, t, _, _, _ in self.syms
                              if not t & N_STAB and t & N_EXT and t & N_TYPE == N_SECT)
        self.imports = {}
        for n, t, _, desc, _ in self.syms:
            if not t & N_STAB and t & N_EXT and t & N_TYPE == N_UNDF:
                ordinal = (desc >> 8) & 0xff
                lib = (self.dylibs[ordinal - 1].split(" (")[0] if 0 < ordinal <= len(self.dylibs)
                       else {0: "self", 0xfe: "executable", 0xff: "flat"}.get(ordinal, f"ordinal {ordinal}"))
                self.imports[n[1:]] = lib

    def section(self, name):
        return next((s for s in self.sections if s["name"] == name), None)

    def functions(self):
        """{name: size} of the symbols in __TEXT,__text, each up to the next one"""
        index = next((i + 1 for i, s in enumerate(self.sections) if s["name"] == "__TEXT,__text"), None)
        if index is None:
            return {}
        text = self.sections[index - 1]
        starts = sorted({(v, n) for n, t, sect, _, v in self.syms
                         if not t & N_STAB and t & N_TYPE == N_SECT and sect == index})
        ends = [v for v, _ in starts[1:]] + [text["addr"] + text["size"]]
        return {n: e - v for (v, n), e in zip(starts, ends)}

    def selectors(self):
        """the method name each __objc_selrefs entry points at. The linker writes each entry as a
        chained rebase whose low 36 bits are the target's address; every one has to land in
        __objc_methname, or the list isn't trusted."""
        refs, names = self.section("__DATA,__objc_selrefs"), self.section("__TEXT,__objc_methname")
        if refs is None or names is None:
            raise SystemExit(f"{self.path}: no __objc_selrefs or __objc_methname")
        # the driver's bridge sends hundreds of selectors: an empty or torn table is a broken
        # reading, never a pass
        if refs["size"] == 0 or refs["size"] % 8:
            raise SystemExit(f"{self.path}: __objc_selrefs is {refs['size']} bytes, not a nonempty list of 8-byte entries")
        out = []
        for i in range(refs["size"] // 8):
            raw, = struct.unpack_from("<Q", self.data, refs["offset"] + i * 8)
            target = raw & 0xFFFFFFFFF
            if not names["addr"] <= target < names["addr"] + names["size"]:
                raise SystemExit(f"{self.path}: selector reference {i} ({raw:#x}) isn't into __objc_methname")
            out.append(cstr(self.data, names["offset"] + target - names["addr"]))
        return out


def named(functions):
    return {n for n in functions if not ARTIFACT.search(n)}


def gate(mine, theirs, mine_json, theirs_json):
    r = Report()
    print(f"### `{os.path.basename(mine.path)}` against the engine's\n")
    r.check(mine.filetype == theirs.filetype == 6, "a dylib, as the engine's", "not a dylib like the engine's")
    r.check(mine.install_name == theirs.install_name, f"install name `{mine.install_name}`",
            f"install name `{mine.install_name}`, the engine's is `{theirs.install_name}`")
    r.check(mine.rpaths == theirs.rpaths, f"rpaths {mine.rpaths}", f"rpaths {mine.rpaths}, the engine's are {theirs.rpaths}")
    r.check(mine.dylibs == theirs.dylibs, f"loads the engine's {len(theirs.dylibs)} libraries: {listing(theirs.dylibs, 20)}",
            f"loads {mine.dylibs}, the engine's loads {theirs.dylibs}")
    r.check(mine.minos is not None and mine.minos == theirs.minos, f"minos {mine.minos} (SDK {mine.sdk}, the engine's {theirs.sdk})",
            f"minos {mine.minos}, the engine's is {theirs.minos}")
    r.check(mine.exports == theirs.exports, f"exports the engine's {len(theirs.exports)} symbols: {listing(theirs.exports, 5)}",
            f"exports differ: missing {listing(set(theirs.exports) - set(mine.exports))}; "
            f"extra {listing(set(mine.exports) - set(theirs.exports))}")
    mi = {n: l for n, l in mine.imports.items() if n not in FIX_IMPORTS}
    ti = {n: l for n, l in theirs.imports.items() if n not in FIX_IMPORTS}
    if mi == ti:
        r.ok(f"imports the engine's {len(ti)} symbols, each from the same library, besides the fix's "
             f"{listing(FIX_IMPORTS)}")
    else:
        diff = {f"{n} ({mi.get(n)} / {ti.get(n)})" for n in set(mi) | set(ti) if mi.get(n) != ti.get(n)}
        r.fail(f"imports differ (mine / the engine's): {listing(diff)}")
    print(f"- report, not the gate: of the fix's imports, mine has {listing(FIX_IMPORTS & set(mine.imports)) or 'none'}, "
          f"the engine's {listing(FIX_IMPORTS & set(theirs.imports)) or 'none'}")
    segments = lambda m: [(s["name"], s["maxprot"], s["initprot"]) for s in m.segments]
    sections = lambda m: sorted((s["name"], s["type"], s["flags"]) for s in m.sections)
    r.check(segments(mine) == segments(theirs), f"the engine's {len(theirs.segments)} segments by name, order and kind",
            f"segments differ: mine {segments(mine)}, the engine's {segments(theirs)}")
    r.check(sections(mine) == sections(theirs), f"the engine's {len(theirs.sections)} sections by name and kind",
            f"sections differ: mine {sections(mine)}, the engine's {sections(theirs)}")
    fm, ft = mine.functions(), theirs.functions()
    nm, nt = named(fm), named(ft)
    r.check(nm == nt, f"the engine's {len(nt)} named functions",
            f"functions differ by name: missing {listing(nt - nm)}; extra {listing(nm - nt)}")
    print(f"- report, not the gate: names the compiler made up, mine {len(fm) - len(nm)}, the engine's {len(ft) - len(nt)}")
    sized = sorted(n for n in nm & nt if fm[n] != ft[n])
    print(f"- report, not the gate: {len(sized)} of {len(nt)} named functions differ in size"
          + (f": {listing(sized, 40)}" if sized else ""))
    sels, tsels = mine.selectors(), theirs.selectors()
    r.check(SELECTOR not in sels, f"none of the {len(sels)} entries of __objc_selrefs is `{SELECTOR}`",
            f"__objc_selrefs has `{SELECTOR}` {sels.count(SELECTOR)} time(s) among {len(sels)} entries")
    # every other selector the engine's file sends, and no other: a table read short can't pass
    want = set(tsels) - {SELECTOR}
    r.check(set(sels) - {SELECTOR} == want, f"the other {len(want)} selectors are the engine's",
            f"selectors differ: missing {listing(want - set(sels))}; extra {listing(set(sels) - want - {SELECTOR})}")
    with open(mine_json) as f:
        jm = json.load(f)
    with open(theirs_json) as f:
        jt = json.load(f)
    r.check(jm == jt, f"the ICD manifest is the engine's: {json.dumps(jt, sort_keys=True)}",
            f"the ICD manifest is {json.dumps(jm, sort_keys=True)}, the engine's {json.dumps(jt, sort_keys=True)}")
    return r.failed


# objects

def norm(path, tree_dir, src, build):
    a = os.path.normpath(os.path.join(tree_dir, path))
    if a.startswith(src + "/"):
        return "src:" + a[len(src) + 1:]
    if a.startswith(build + "/"):
        return "build:" + a[len(build) + 1:]
    return "sys:" + a


def parse_deps(text):
    """ninja -t deps: '<output>: #deps N, ...' and then one indented input a line"""
    deps, cur = {}, None
    for line in text.splitlines():
        if line.startswith((" ", "\t")):
            if cur is not None:
                deps[cur].append(line.strip())
        elif line.strip():
            cur = line.split(": #deps", 1)[0]
            deps[cur] = []
    return deps


def record(out, src, build):
    src, build = os.path.realpath(src), os.path.realpath(build)
    with open(os.path.join(out, "deps.txt")) as f:
        deps = parse_deps(f.read())
    with open(os.path.join(out, "inputs.txt")) as f:
        objects = sorted({l.strip() for l in f if l.strip().endswith(".o")})
    with open(os.path.join(out, "compdb.json")) as f:
        compdb = {e.get("output"): e for e in json.load(f)}
    rec, bad = {"objects": {}, "generated": {}}, []
    if FIX_OBJECT not in objects:
        bad.append(f"`{FIX_OBJECT}` isn't among the {len(objects)} objects ninja says the dylib is built from")
    for obj in objects:
        path = os.path.join(build, obj)
        if not os.path.exists(path):
            bad.append(f"`{obj}`: not built")
            continue
        e = compdb.get(obj)
        inputs = sorted({norm(p, build, src, build) for p in deps.get(obj, [])}
                        | ({norm(e["file"], e.get("directory", build), src, build)} if e else set()))
        rec["objects"][obj] = {"sha256": sha256(path), "command": e["command"] if e else None, "inputs": inputs}
        for i in inputs:
            if i.startswith("build:") and i not in rec["generated"]:
                p = os.path.join(build, i[len("build:"):])
                rec["generated"][i] = sha256(p) if os.path.isfile(p) else None
    with open(os.path.join(out, "record.json"), "w") as f:
        json.dump(rec, f, indent=1, sort_keys=True)
    print(f"recorded {len(rec['objects'])} objects, {len(rec['generated'])} generated inputs")
    for b in bad:
        print(f"- **FAIL**: {b}")
    return 1 if bad else 0


def compare_objects(base, head, changed):
    """(failures, touched {object: (reasons, same bytes)}, new, removed, untouched count)"""
    def changed_src(i):
        p = i[len("src:"):]
        return any(p == c or p.startswith(c.rstrip("/") + "/") for c in changed)
    b, h = base["objects"], head["objects"]
    bad, touched, new, removed, untouched = [], {}, [], [], 0
    for obj in sorted(set(b) | set(h)):
        if obj not in h:
            removed.append(obj)
            continue
        if obj not in b:
            new.append(obj)
            continue
        inputs = set(b[obj]["inputs"]) | set(h[obj]["inputs"])
        if not inputs:
            bad.append(f"`{obj}`: no record of its inputs")
            continue
        reasons = sorted(i for i in inputs
                         if (i.startswith("src:") and changed_src(i))
                         or (i.startswith("build:") and base["generated"].get(i) != head["generated"].get(i)))
        # both builds come from one meson setup, so a compile command that differs means the trees
        # aren't comparable, not that the commit touched the object
        if b[obj]["command"] != h[obj]["command"]:
            bad.append(f"`{obj}`: its compile command isn't the base build's")
            continue
        same = b[obj]["sha256"] == h[obj]["sha256"]
        if reasons:
            touched[obj] = (reasons, same)
        elif same:
            untouched += 1
        else:
            bad.append(f"`{obj}`: none of its inputs changed, and it isn't the base build's")
    return bad, touched, new, removed, untouched


def patched(base_dir, head_dir, changed):
    with open(os.path.join(base_dir, "record.json")) as f:
        base = json.load(f)
    with open(os.path.join(head_dir, "record.json")) as f:
        head = json.load(f)
    bad, touched, new, removed, untouched = compare_objects(base, head, changed)
    differ = sorted(o for o, (_, same) in touched.items() if not same)
    print("### Objects against my build of the engine's commit\n")
    print(f"- changed paths: {listing(changed, 100)}")
    print(f"- {untouched} of {len(set(base['objects']) & set(head['objects']))} objects in both builds: none of "
          f"their inputs changed, and each is the base build's byte for byte")
    # a compare where nothing is held to the base's bytes, or the dylib's objects changed, proves nothing
    if not untouched:
        bad.append("no object is untouched, so none was held to the base build's bytes")
    for title, items in (("new", new), ("gone", removed)):
        if items:
            bad.append(f"{title} objects, which this compare can't hold to a base: {listing(items, 20)}")
    for x in bad:
        print(f"- **FAIL**: {x}")
    print(f"- differ from the base build: {len(differ)}" + (f": {listing(differ, 100)}" if differ else ""))
    by_reason = {}
    for obj, (reasons, same) in touched.items():
        by_reason.setdefault(tuple(reasons), []).append((obj, same))
    print(f"- touched by this commit: {len(touched)}" + (", by what touched them:" if touched else ""))
    for reasons, objs in sorted(by_reason.items()):
        print(f"  - through {', '.join(f'`{x}`' for x in reasons[:5])}" + (" and more" if len(reasons) > 5 else "") + ":")
        for obj, same in sorted(objs):
            print(f"    - `{obj}`" + (" (still the base's bytes)" if same else " (differs)"))
    for title, items in (("new", new), ("gone", removed)):
        print(f"- {title} objects: {len(items)}" + (f": {listing(items, 100)}" if items else ""))
    return 1 if bad else 0


def flags(build, source):
    build = os.path.realpath(build)
    with open(os.path.join(build, "compile_commands.json")) as f:
        entries = json.load(f)
    for e in entries:
        if os.path.realpath(os.path.join(e["directory"], e["file"])).endswith("/" + source):
            args = shlex.split(e["command"])
            out, skip = [], False
            # without the compiler, which the caller names
            for a in args[1:]:
                if skip:
                    skip = False
                elif a in ("-o", "-MF", "-MQ", "-MT"):
                    skip = True
                elif a in ("-c", "-MD") or a == e["file"]:
                    pass
                else:
                    out.append(a)
            # include paths are relative to the build directory
            out = [f"-I{os.path.join(build, a[2:])}" if a.startswith("-I") and not os.path.isabs(a[2:]) else a for a in out]
            sys.stdout.write("\0".join(out))
            return 0
    print(f"no compile command for {source} in {build}/compile_commands.json", file=sys.stderr)
    return 1


# selftest

def selftest(engine, engine_json):
    import contextlib
    import io
    with open(engine, "rb") as f:
        data = f.read()
    m = MachO(engine, data)
    status = []

    def run(label, blob, want_fail, needle):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            failed = gate(MachO(label, blob), m, engine_json, engine_json)
        out = buf.getvalue()
        good = (failed > 0) == want_fail and (needle in out)
        print(f"{'ok' if good else 'WRONG'}: {label}: {failed} failed check(s)")
        if not good:
            print(out)
        status.append(good)
        return out

    # the file against itself: every check but the selector one holds
    out = run("the engine's file", data, True, f"__objc_selrefs has `{SELECTOR}` 1 time(s)")
    status.append(out.count("**FAIL**") == 1)

    def patched_copy(edit):
        d = bytearray(data)
        edit(d)
        return bytes(d)

    def sym_index(pred):
        return next(i for i, (n, t, s, _, _) in enumerate(m.syms) if pred(n, t, s))

    def entry(i):
        return m.symoff + i * 16

    # another minos
    build = next(o for c, o, _ in m.commands if c == LC_BUILD_VERSION)
    run("another minos", patched_copy(lambda d: struct.pack_into("<I", d, build + 12, (26 << 16) | (5 << 8))),
        True, "minos 26.5")
    # an export made local
    exp = sym_index(lambda n, t, s: n == "_vk_icdGetInstanceProcAddr" and t & N_EXT)
    run("an export made local", patched_copy(lambda d: d.__setitem__(entry(exp) + 4, d[entry(exp) + 4] & ~N_EXT)),
        True, "exports differ")
    # a named function renamed (one letter of its string)
    fn = sym_index(lambda n, t, s: n == "_mtl_new_texture_with_descriptor_linear")
    strx = struct.unpack_from("<I", data, entry(fn))[0]
    stroff = next(struct.unpack_from("<IIII", data, o + 8)[2] for c, o, _ in m.commands if c == LC_SYMTAB)
    run("a function renamed", patched_copy(lambda d: d.__setitem__(stroff + strx + 1, ord("M"))),
        True, "functions differ by name")
    # a compiler-made name renumbered: not the gate
    art = sym_index(lambda n, t, s: n == "_OUTLINED_FUNCTION_0")
    astrx = struct.unpack_from("<I", data, entry(art))[0]
    out = run("a made-up name renumbered", patched_copy(lambda d: d.__setitem__(stroff + astrx + len("_OUTLINED_FUNCTION_0") - 1, ord("9"))),
        True, f"__objc_selrefs has `{SELECTOR}` 1 time(s)")
    status.append("functions differ by name" not in out)
    # another library: libz.1 becomes libz.2
    lib = data.index(b"/usr/lib/libz.1.dylib\0")
    run("another library", patched_copy(lambda d: d.__setitem__(lib + len("/usr/lib/libz."), ord("2"))),
        True, "loads [")
    # the selector renamed in __objc_methname: another selector than the engine's, which fails
    names = m.section("__TEXT,__objc_methname")
    at = data.index(SELECTOR.encode() + b"\0", names["offset"], names["offset"] + names["size"])
    run("the selector's name changed", patched_copy(lambda d: d.__setitem__(at, ord("S"))),
        True, "extra `SparseBufferTier`")
    # what my build should be: the selector's one entry gone (here pointing at another selector's
    # name), and the fix's import gone. Every check holds.
    refs = m.section("__DATA,__objc_selrefs")
    i = m.selectors().index(SELECTOR)
    first = data[refs["offset"]:refs["offset"] + 8]
    imp = sym_index(lambda n, t, s: n == "_objc_opt_respondsToSelector")

    def fixed(d):
        d[refs["offset"] + i * 8:refs["offset"] + i * 8 + 8] = first
        # (the import made a debugger-only symbol, which neither list reads)
        d[entry(imp) + 4] = 0x20
    out = run("the fix's file", patched_copy(fixed), False, "none of the 296 entries")
    status.append("mine has none, the engine's `objc_opt_respondsToSelector`" in out)
    # an empty selector table: not a pass
    refs_hdr = next(o for c, o, _ in m.commands if c == LC_SEGMENT_64 and data[o + 8:o + 24].rstrip(b"\0") == b"__DATA")
    nsects = struct.unpack_from("<I", data, refs_hdr + 64)[0]
    sect = next(refs_hdr + 72 + i * 80 for i in range(nsects)
                if data[refs_hdr + 72 + i * 80:refs_hdr + 72 + i * 80 + 16].rstrip(b"\0") == b"__objc_selrefs")
    try:
        MachO("an empty selector table", patched_copy(lambda d: struct.pack_into("<Q", d, sect + 40, 0))).selectors()
        good = False
    except SystemExit as e:
        good = "not a nonempty list" in str(e)
    print(f"{'ok' if good else 'WRONG'}: an empty selector table is refused")
    status.append(good)
    # a table read short: the selector set isn't the engine's
    run("a selector table read short", patched_copy(lambda d: struct.pack_into("<Q", d, sect + 40, 266 * 8)), True,
        "selectors differ: missing")
    # another ICD manifest
    with tempfile.TemporaryDirectory() as t:
        other = os.path.join(t, "icd.json")
        with open(engine_json) as f:
            j = json.load(f)
        j["ICD"]["library_path"] = "libvulkan_kosmickrisp.dylib"
        with open(other, "w") as f:
            json.dump(j, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            failed = gate(MachO("another manifest", data), m, other, engine_json)
        good = "the ICD manifest is {" in buf.getvalue() and failed == 2
        print(f"{'ok' if good else 'WRONG'}: another manifest: {failed} failed check(s)")
        status.append(good)
    # the object compare on made-up records: an untouched object with other bytes fails, a touched
    # one passes, and a changed generated header touches what read it
    obj = lambda sha, inputs, cmd="cc": {"sha256": sha, "command": cmd, "inputs": inputs}
    base = {"objects": {"a.o": obj("1", ["src:a.c"]), "b.o": obj("2", ["src:b.c", "build:g.h"]),
                        "c.o": obj("3", ["src:c.c"])}, "generated": {"build:g.h": "x"}}
    head = {"objects": {"a.o": obj("1", ["src:a.c"]), "b.o": obj("9", ["src:b.c", "build:g.h"]),
                        "c.o": obj("8", ["src:c.c"])}, "generated": {"build:g.h": "y"}}
    bad, touched, _, _, untouched = compare_objects(base, head, ["c.c"])
    good = (untouched == 1 and set(touched) == {"b.o", "c.o"} and not bad)
    head["generated"]["build:g.h"] = "x"
    bad, touched, _, _, _ = compare_objects(base, head, ["c.c"])
    good = good and bad == ["`b.o`: none of its inputs changed, and it isn't the base build's"]
    # another compile command fails the object, it doesn't excuse it
    head["generated"]["build:g.h"] = "y"
    head["objects"]["a.o"]["command"] = "cc -O0"
    bad, _, _, _, untouched = compare_objects(base, head, ["c.c"])
    good = good and bad == ["`a.o`: its compile command isn't the base build's"] and untouched == 0
    print(f"{'ok' if good else 'WRONG'}: the object compare on made-up records")
    status.append(good)
    print("\n" + ("all checks passed" if all(status) else "failed: the self-test"))
    return 0 if all(status) else 1


def main():
    a = sys.argv[1:]
    if a[:1] == ["gate"] and len(a) == 5:
        failed = gate(MachO(a[1]), MachO(a[2]), a[3], a[4])
        print("\n" + (f"failed: {failed} check(s)" if failed else "all checks passed"))
        return 1 if failed else 0
    if a[:1] == ["record"] and len(a) == 4:
        return record(a[1], a[2], a[3])
    if a[:1] == ["patched"] and len(a) >= 3:
        status = patched(a[1], a[2], a[3:])
        print("\n" + ("failed: the object compare" if status else "all checks passed"))
        return status
    if a[:1] == ["flags"] and len(a) == 3:
        return flags(a[1], a[2])
    if a[:1] == ["selftest"] and len(a) == 3:
        return selftest(a[1], a[2])
    print("usage: compare.py gate <mine.dylib> <engine.dylib> <mine.json> <engine.json>\n"
          "       compare.py record <record dir> <source dir> <build dir>\n"
          "       compare.py patched <base record dir> <head record dir> [<changed path>...]\n"
          "       compare.py flags <build dir> <source path>\n"
          "       compare.py selftest <engine.dylib> <engine.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
