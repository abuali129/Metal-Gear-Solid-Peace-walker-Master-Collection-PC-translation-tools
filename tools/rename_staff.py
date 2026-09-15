"""Rename the soldiers already on Mother Base, using the Staff tab.

    python tools/rename_staff.py <project> <STW save> [out dir]

The table in STAGEDAT only names *new* recruits: a soldier's codename is
copied into the save when he is fultoned, so a roster built before the
translation stays English.  This walks the save and rewrites every codename
the Staff tab has a translation for.

Without an output directory it only reports -- nothing is written.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwtr import pwsave                                       # noqa: E402
from pwtr.project import FACE, Project                        # noqa: E402


def main(argv):
    if not 2 <= len(argv) <= 3:
        print(__doc__)
        return 2
    project = Project.load(argv[0])
    save = pwsave.Save.load(argv[1])
    out_dir = argv[2] if len(argv) > 2 else None

    entries = (project.read("staff")["entries"]
               if project.has("staff") else [])
    arabic = {e["source"]: e["target"] for e in entries if e.get("target")}
    if not arabic:
        print("Nothing translated in the Staff tab.")
        return 1

    done, over, unknown = 0, [], {}
    for slot, raw in save.roster():
        english = raw.decode("latin-1")
        target = arabic.get(english)
        if target is None:
            unknown[english] = unknown.get(english, 0) + 1
            continue
        written = project.render(target, FACE["staff"]).encode("utf-8")
        if len(written) > pwsave.BUDGET:
            over.append((english, target, len(written)))
            continue
        save.rename(slot, written)
        done += 1

    print("%d soldier(s) renamed" % done)
    for english, target, need in sorted(set(over)):
        print("   ! %s -> %s needs %d of %d bytes"
              % (english, target, need, pwsave.BUDGET))
    left = sum(unknown.values())
    if left:
        print("%d soldier(s) left alone -- %d name(s) not translated yet"
              % (left, len(unknown)))
        for english in sorted(unknown)[:10]:
            print("     %s (x%d)" % (english, unknown[english]))

    if out_dir is None:
        print("\nNothing written.  Pass an output folder to save.")
        return 0
    written = save.write(out_dir)
    print("\nwritten -> %s" % written)
    print("Copy it into the game's ww folder under exactly that name, with "
          "the game closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
