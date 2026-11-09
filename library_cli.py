# library_cli.py
"""
Browse / tag / organize the library .

  python3 library_cli.py autotag [--dry-run]              fill tags, sample type and BPM from file names + folders
  python3 library_cli.py search "dream" -t beat -t 2024 --bpm 120-140 --sort bpm
  python3 library_cli.py search -t trap --exclude-tag demo --key 8A
  python3 library_cli.py tags                             all tags with counts
  python3 library_cli.py tag 12 kalimba dark              add tags to track 12
  python3 library_cli.py untag 12 dark
  python3 library_cli.py rename-tag "hip-hop" "hip hop"
  python3 library_cli.py collect "Summer EP" 12 15 20     add tracks to a collection
  python3 library_cli.py collections
  python3 library_cli.py search -c "Summer EP"
"""
import argparse
import sys

from audio_engine.db import init_db
from audio_engine import library, search


def _bpm_range(s):
    if not s:
        return None, None, None
    if "-" in s:
        lo, hi = s.split("-", 1)
        return None, float(lo), float(hi)
    return float(s), None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("autotag"); a.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("search")
    s.add_argument("text", nargs="?"); s.add_argument("-t", "--tag", action="append", default=[])
    s.add_argument("--exclude-tag", action="append", default=[]); s.add_argument("--key")
    s.add_argument("--bpm", help="exact (120) or range (120-140)"); s.add_argument("--type", dest="sample_type",
                   choices=["loop", "one-shot", "track"]); s.add_argument("-c", "--collection")
    s.add_argument("--sort", default="name", choices=sorted(search.SORTS)); s.add_argument("--desc", action="store_true")
    s.add_argument("--page", type=int, default=1); s.add_argument("--per-page", type=int, default=25)
    s.add_argument("--top-tags", type=int, default=15)
    sub.add_parser("tags")
    t = sub.add_parser("tag"); t.add_argument("track_id", type=int); t.add_argument("tags", nargs="+")
    u = sub.add_parser("untag"); u.add_argument("track_id", type=int); u.add_argument("tags", nargs="+")
    r = sub.add_parser("rename-tag"); r.add_argument("old"); r.add_argument("new")
    c = sub.add_parser("collect"); c.add_argument("name"); c.add_argument("track_ids", type=int, nargs="+")
    sub.add_parser("collections")
    args = ap.parse_args()

    init_db()
    if args.cmd == "autotag":
        st = search.autotag_library(dry_run=args.dry_run)
        print(("[dry run] " if args.dry_run else "") + ", ".join(f"{k}={v}" for k, v in st.items()))
    elif args.cmd == "search":
        bpm, lo, hi = _bpm_range(args.bpm)
        res = search.search_tracks(args.text, args.tag, args.exclude_tag, args.key, bpm, lo, hi,
                                   args.sample_type, args.collection, args.sort, args.desc, args.page, args.per_page)
        print(f"{res['total']} match(es) - page {res['page']}")
        for tr in res["tracks"]:
            bpm_s = f"{tr['bpm']:.0f}" if tr["bpm"] else "-"
            print(f"  #{tr['id']:<5} {bpm_s:>4} bpm  {tr['sample_type'] or '-':<8} {tr['filename'][:48]:<48}  {' '.join(tr['tags'])}")
        top = res["tag_summary"][:args.top_tags]
        if top:
            print("tags in this result: " + "  ".join(f"{x['tag']}({x['count']})" for x in top))
    elif args.cmd == "tags":
        for x in library_tags():
            print(f"{x['count']:>6}  {x['tag']}")
    elif args.cmd == "tag":
        print(", ".join(library.tag_track(args.track_id, args.tags)))
    elif args.cmd == "untag":
        search.untag_track(args.track_id, args.tags)
    elif args.cmd == "rename-tag":
        print(f"{search.rename_tag(args.old, args.new)} track(s) updated")
    elif args.cmd == "collect":
        print(f"{search.add_to_collection(args.name, args.track_ids)} added to '{args.name}'")
    elif args.cmd == "collections":
        for x in search.list_collections():
            print(f"{x['track_count']:>6}  {x['name']}")
    return 0


def library_tags():
    return search.list_tags()


if __name__ == "__main__":
    sys.exit(main())
