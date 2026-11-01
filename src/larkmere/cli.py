"""Command line entry point."""

import argparse
import sys
from pathlib import Path

DEFAULT_OUT = Path("out")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="larkmere", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="run everything on sample data")
    demo.add_argument("--out", type=Path, default=DEFAULT_OUT)
    demo.add_argument("--mode", choices=("offline", "replay", "live", "record"),
                      help="offline: rule-based stand-ins, no AI. replay: recorded model responses (the default "
                           "when recordings exist). live: call the model (needs .[live] and a key). "
                           "record: live, and save the responses.")
    demo.add_argument("--approve-as", metavar="NAME",
                      help="skip the review page: approve clean notes and reject flagged ones under this name")
    demo.add_argument("--no-robot", action="store_true", help="skip the steps that use the mouse and keyboard")
    demo.add_argument("--yes", action="store_true", help="don't ask before the robot takes the mouse")
    demo.add_argument("--no-browser", action="store_true")

    crm = commands.add_parser("crm", help="open the demo CRM and click around it yourself")
    crm.add_argument("--db", type=Path, default=DEFAULT_OUT / "larkmere.sqlite")

    site = commands.add_parser("site", help="rebuild the site from the last demo run")
    site.add_argument("--out", type=Path, default=DEFAULT_OUT)

    args = parser.parse_args(argv)
    if args.command == "demo":
        from larkmere import pipeline
        pipeline.run(args.out, mode=args.mode or pipeline.default_mode(), reviewer=args.approve_as, use_robot=not args.no_robot,
                     assume_yes=args.yes, open_browser=not args.no_browser)
    elif args.command == "crm":
        from larkmere import world
        from larkmere.crm import app
        if not args.db.exists():
            world.build(args.db)
        app.main(["--db", str(args.db)])
    elif args.command == "site":
        import json
        from larkmere import site as site_module
        from larkmere.revenue import metrics
        results = json.loads((args.out / "results.json").read_text(encoding="utf-8"))
        print(site_module.build(args.out, metrics.compute(args.out / "larkmere.sqlite"), results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
