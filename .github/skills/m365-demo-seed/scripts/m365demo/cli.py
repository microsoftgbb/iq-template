"""Command line: every command prints one JSON object on stdout.

Exit codes: 0 done, 1 usage error, 2 blocked (investigate), 3 waiting for a person (relay `next`).
"""

import argparse
import sys
import traceback

from .common import BLOCKED, CLI, OK, WAITING, Stop, Usage, emit
from . import VERSION, demo, local, pages, receipts, runs, sample, status, transport, users, workiq, workspace

PERSON_STOPS = frozenset({"sign-in-expired", "sign-in-declined", "wrong-account", "needs-passwords", "consent-missing"})


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Usage("usage", f"{message} (see {CLI} --help)")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--receipts-file", metavar="PATH",
                        help="read the m365_demo receipt from this saved `deploy.py status --json` output")
    who = argparse.ArgumentParser(add_help=False)
    who.add_argument("--demo", metavar="NAME", help="the demo whose people to use (default: the latest demo)")
    who.add_argument("--upn", action="append", metavar="UPN", help="a demo user (repeatable); overrides --demo")
    named = argparse.ArgumentParser(add_help=False)
    named.add_argument("--demo", metavar="NAME", help="the demo (default: the latest demo)")
    gated = argparse.ArgumentParser(add_help=False)
    gated.add_argument("--tenant", required=True, metavar="TENANT_ID",
                       help="the deployment tenant, which the user confirmed is a non-production demo tenant")
    one_run = argparse.ArgumentParser(add_help=False)
    one_run.add_argument("--run", required=True, metavar="RUN_ID", help="a run id from `history`")

    parser = Parser(prog="m365demo.py", description="Seed fictional Microsoft 365 content into this deployment's "
                    "demo tenant for Work IQ demos. Inspired by DemoIQ (https://aka.ms/demoiq).")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("status", parents=[common, named], help="where seeding stands and the next step")

    demos = commands.add_parser("demo", help="author, validate and preview demos").add_subparsers(
        dest="action", required=True, metavar="ACTION")
    new = demos.add_parser("new", help="start a demo from the template's sample story")
    new.add_argument("name", help="a short name: lowercase letters, digits and '-'")
    new.add_argument("--admin", required=True, metavar="UPN", help="the administrator who signs in for each run")
    new.add_argument("--person", action="append", required=True, metavar="UPN[=NAME]",
                     help="a demo user, optionally with a display name (2 to 8)")
    new.add_argument("--title", help="the demo's title")
    new.add_argument("--force", action="store_true", help="replace an existing demo of that name")
    demos.add_parser("validate", parents=[named], help="check a demo and print its approval hash")
    demos.add_parser("preview", parents=[common, named], help="write the content preview page for approval")
    demos.add_parser("list", help="the demos in the workspace")

    user_commands = commands.add_parser("users", help="demo users' passwords and sign-in check").add_subparsers(
        dest="action", required=True, metavar="ACTION")
    setter = user_commands.add_parser("set", parents=[who], help="type the passwords in your own terminal (masked)")
    setter.add_argument("--missing-only", action="store_true", help="only users without a stored password")
    user_commands.add_parser("status", parents=[who], help="which demo users have a stored password")
    user_commands.add_parser("check", parents=[common, who], help="sign each demo user in and read their licenses")
    user_commands.add_parser("clear", parents=[who], help="forget stored passwords (all, or --upn ones)")

    seed = commands.add_parser("seed", parents=[common, gated], help="seed an approved demo (a detached run)")
    seed.add_argument("--demo", required=True, metavar="NAME")
    seed.add_argument("--approve", required=True, metavar="SHA256", help="the preview's hash the user approved")
    seed.add_argument("--again", action="store_true", help="seed a demo that an earlier run already seeded")
    commands.add_parser("resume", parents=[common, gated, one_run], help="continue an interrupted or failed seed run")
    commands.add_parser("restore", parents=[common, gated, one_run], help="restore tenant settings a run left changed")
    watch = commands.add_parser("monitor", parents=[one_run], help="a run's state and progress")
    watch.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                       help="wait up to this long (at most 600) for the state to change")
    commands.add_parser("cancel", parents=[one_run], help="stop a run after its current item and restore the tenant")
    clean = commands.add_parser("cleanup", parents=[common, one_run],
                                help="plan, or with --tenant and --approve start, deleting what a seed run created")
    clean.add_argument("--tenant", metavar="TENANT_ID", help="the deployment tenant the user confirmed (to start)")
    clean.add_argument("--approve", metavar="SHA256", help="the cleanup plan's hash the user approved (to start)")
    past = commands.add_parser("history", help="past runs")
    past.add_argument("--limit", type=int, default=20)

    check = commands.add_parser("workiq", help="the optional Work IQ check of a seed run").add_subparsers(
        dest="action", required=True, metavar="ACTION")
    asked = check.add_parser("prompts", parents=[one_run], help="the demo's prompts to ask Work IQ, and as whom")
    asked.add_argument("--count", type=int, default=3)
    noted = check.add_parser("record", parents=[one_run], help="score the answers Work IQ gave against the run")
    noted.add_argument("--results", required=True, metavar="PATH",
                       help='JSON: {"askedAs": UPN, "answers": [{"prompt", "answered", "citations": [{"title", "url"}]}]}')

    tidy = commands.add_parser("local-cleanup", help="list, or with --confirm remove, this skill's files on this computer")
    tidy.add_argument("--depth", choices=local.DEPTHS, default="runs",
                      help="runs: run folders only (default); all: also demos and stored passwords")
    tidy.add_argument("--include-uncleaned", action="store_true",
                      help="also remove run folders whose seeded content is still in the tenant")
    tidy.add_argument("--confirm", metavar="TOKEN", help="the inventory's confirmToken, after the user agreed")
    return parser


def run_exit(report):
    state = report.get("state")
    reason = (report.get("result") or {}).get("reason")
    if state == "waiting-for-sign-in" or (state == "failed" and reason in PERSON_STOPS):
        return WAITING
    if state in ("failed", "interrupted", "needs-restore"):
        return BLOCKED
    return OK


def parse_people(values):
    found = []
    for value in values:
        upn, _, name = value.partition("=")
        upn = upn.strip().lower()
        if not demo.UPN.match(upn):
            raise Usage("bad-upn", f"Not a user principal name: {upn!r}.")
        found.append((upn, name.strip() or None))
    if not demo.PEOPLE[0] <= len(found) <= demo.PEOPLE[1]:
        raise Usage("people", f"A demo needs {demo.PEOPLE[0]} to {demo.PEOPLE[1]} demo users (--person).")
    if len({upn for upn, _ in found}) != len(found):
        raise Usage("people", "A demo user is listed twice.")
    return found


def demo_command(args):
    if args.action == "list":
        return {"demos": demo.listing()}
    if args.action == "new":
        name = workspace.check_name(args.name, "demo name")
        admin = args.admin.strip().lower()
        if not demo.UPN.match(admin):
            raise Usage("bad-upn", f"Not a user principal name: {args.admin!r}.")
        people = parse_people(args.person)
        if admin in {upn for upn, _ in people}:
            raise Usage("people", "The administrator cannot also be a demo user.")
        if demo.file(name).exists() and not args.force:
            raise Usage("exists", f"The demo {name!r} exists; choose another name or add --force.")
        workspace.write_json(demo.file(name), sample.build(admin, people, args.title))
        return {**demo.report(name), "next": [
            f"Adapt the story in {demo.file(name)} to the user's scenario (see references/content.md), then: "
            f"{CLI} demo preview --demo {name}"]}
    name = args.demo or demo.latest()
    if not name:
        raise Usage("no-demo", "There is no demo yet.", next=[f"{CLI} demo new NAME --admin UPN --person UPN ..."])
    report = demo.report(workspace.check_name(name, "demo name"))
    if args.action == "validate":
        return report
    if not report["valid"]:
        raise Usage("invalid-demo", f"The demo {name!r} has {len(report['errors'])} problem(s).", errors=report["errors"])
    try:
        receipt = receipts.load(args.receipts_file)
    except Stop:
        receipt = None
    page = workspace.demo_dir(name) / "preview.html"
    workspace.write_private(page, pages.preview(name, demo.load(name), report["sha256"],
                                                receipt and receipt["tenant_id"]))
    tenant = receipt["tenant_id"] if receipt else "TENANT_ID"
    return {**report, "preview": str(page), "next": [
        f"Ask the user to open {page} themselves, review the content and the tenant changes, and approve.",
        f"After approval: {CLI} seed --demo {name} --tenant {tenant} --approve {report['sha256']}"]}


def users_command(args):
    if args.action == "clear":
        return users.clear(users.targets(args.upn, args.demo) if (args.upn or args.demo) else None)
    upns = users.targets(args.upn, args.demo)
    if args.action == "set":
        selector = (" --demo " + args.demo) if args.demo else "".join(f" --upn {upn}" for upn in args.upn or [])
        return users.set_passwords(upns, command=f"{CLI} users set{selector}", missing_only=args.missing_only)
    if args.action == "status":
        return users.status(upns)
    if not upns:
        raise Usage("no-users", "Name the demo users: --demo NAME, or --upn UPN for each.")
    return users.check(upns, users.require_receipt(args.receipts_file))


def run(args):
    """(result, exit code) for one command."""
    transport.testing()  # a misdirected endpoint override stops every command before any request
    if args.command == "status":
        return status.report(args.receipts_file, args.demo), OK
    if args.command == "demo":
        return demo_command(args), OK
    if args.command == "users":
        return users_command(args), OK
    if args.command == "history":
        return {"runs": runs.history(max(1, min(args.limit, 200)))}, OK
    if args.command == "cancel":
        return runs.cancel(args.run), OK
    if args.command == "workiq":
        run = runs.Run(args.run)
        if args.action == "prompts":
            return workiq.prompts(run, args.count), OK
        return workiq.record(run, args.results), OK
    if args.command == "local-cleanup":
        if args.confirm:
            return local.execute(args.depth, args.confirm, args.include_uncleaned), OK
        return local.inventory(args.depth, args.include_uncleaned), OK
    if args.command == "cleanup" and not (args.tenant or args.approve):
        return runs.cleanup_plan(args.run), OK
    if args.command == "restore":
        report = runs.start_restore(args.run, args.receipts_file, args.tenant)
    elif args.command == "seed":
        report = runs.start_seed(workspace.check_name(args.demo, "demo name"), args.receipts_file, args.tenant,
                                 args.approve, args.again)
    elif args.command == "resume":
        report = runs.resume(args.run, args.receipts_file, args.tenant)
    elif args.command == "monitor":
        report = runs.monitor(args.run, args.wait)
    elif args.command == "cleanup":
        if not (args.tenant and args.approve):
            raise Usage("usage", "To start a cleanup, pass both --tenant and --approve (from the plan the user approved).")
        report = runs.start_cleanup(args.run, args.receipts_file, args.tenant, args.approve)
    else:
        raise Usage("usage", f"Unknown command (see {CLI} --help).")
    return report, run_exit(report)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_worker"] and len(argv) == 2:
        return runs.worker_main(argv[1])
    try:
        result, code = run(build_parser().parse_args(argv))
    except Stop as stop:
        emit(stop.report())
        return stop.code
    except KeyboardInterrupt:
        emit({"ok": False, "reason": "interrupted", "message": "Interrupted."})
        return BLOCKED
    except Exception as error:  # noqa: BLE001 - one JSON answer even for a defect
        traceback.print_exc(file=sys.stderr)
        emit({"ok": False, "reason": "internal-error", "message": f"{type(error).__name__}: {error}"})
        return BLOCKED
    emit({"ok": True, **result})
    return code
