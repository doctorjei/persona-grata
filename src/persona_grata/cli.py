"""Explicit agent lifecycle commands; configuration documents define variants."""

import argparse
import os
from pathlib import Path
import stat
import sys


_TOKEN_LIMIT = 65536


def _parser():
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="persona-grata",
        description="Manage named agents and retrieve their credentials.",
        epilog=("No arguments lists registered agents. A name alone shows its saved "
                "configuration without resolving it. Custom variants are .cfg/.tmpl "
                "documents, not command-line definitions."),
        usage="%(prog)s [options] [NAME | PERSONA HARNESS | SOURCE.cfg]",
    )
    parser.add_argument("selection", nargs="*", metavar="SELECTION")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--create", action="store_true", help="create and install PERSONA HARNESS or SOURCE.cfg/.tmpl")
    modes.add_argument("--update", action="store_true", help="update and reinstall NAME [SOURCE.cfg/.tmpl]")
    modes.add_argument("-r", "--remove", action="store_true", help="remove NAME after confirmation; keep manager credentials")
    modes.add_argument("--export", metavar="FILE", help="export NAME's saved partial configuration only")
    modes.add_argument("--setup", action="store_true", help="configure the agent store and password manager")
    modes.add_argument("--get-secret", metavar="NAME", help="print NAME's credential to stdout (sensitive; read-only)")
    parser.add_argument("--name", metavar="NAME", help="elect a create name, or rename an agent with --update")
    parser.add_argument("--path", metavar="DIR", help="explicit agent directory on --create only")
    parser.add_argument("--store", metavar="DIR", help="store root (otherwise AGENT_STORE_DIR or XDG config agents)")
    token = parser.add_mutually_exclusive_group()
    token.add_argument("--token", metavar="FILE", help="read a token from FILE for create/update; never print it")
    token.add_argument("--no-token", action="store_true", help="set agent.secret to None on create/update")
    parser.add_argument("--verify", action="store_true", help="verify credentials during create/update (may contact the endpoint)")
    parser.add_argument("--no-wrapper", action="store_true", help="create/update without installing a shell wrapper")
    parser.add_argument("--yes", action="store_true", help="confirm --remove without prompting")
    parser.add_argument("--force", action="store_true", help="allow --export to replace an existing destination")
    parser.add_argument("--backend", choices=("pass", "gopass", "prs"), help="password manager for --setup")
    parser.add_argument("--secret-root", metavar="ROOT", help="manager entry prefix for --setup")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _is_source(value):
    return Path(value).suffix.lower() in {".cfg", ".tmpl"}


def _validate_options(parser, args):
    editing = args.create or args.update
    for flag, used, allowed in (
        ("--name", args.name is not None, editing),
        ("--path", args.path is not None, args.create),
        ("--token", args.token is not None, editing),
        ("--no-token", args.no_token, editing),
        ("--verify", args.verify, editing),
        ("--no-wrapper", args.no_wrapper, editing),
        ("--yes", args.yes, args.remove),
        ("--force", args.force, args.export is not None),
        ("--backend", args.backend is not None, args.setup),
        ("--secret-root", args.secret_root is not None, args.setup),
    ):
        if used and not allowed:
            parser.error(f"{flag} does not apply to this mode")

    chosen = args.selection
    if args.get_secret is not None:
        if chosen:
            parser.error("--get-secret takes NAME as its option argument; no positional selection")
    elif args.setup:
        if chosen:
            parser.error("--setup takes no positional selection")
    elif args.create:
        if not (len(chosen) == 2 and not any(map(_is_source, chosen))
                or len(chosen) == 1 and _is_source(chosen[0])):
            parser.error("--create requires PERSONA HARNESS or one SOURCE.cfg/.tmpl")
    elif args.update:
        if not (len(chosen) == 1 or len(chosen) == 2 and _is_source(chosen[1])):
            parser.error("--update requires NAME [SOURCE.cfg/.tmpl]")
    elif args.remove or args.export is not None:
        if len(chosen) != 1:
            parser.error("--remove and --export require exactly one installed agent NAME")
    elif len(chosen) > 1:
        parser.error("select an explicit --create or --update mode to install an agent")


def _read_token(filename):
    """Read a bounded regular file; do not quote its contents in diagnostics."""
    path = Path(filename).expanduser()
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("--token requires a regular file")
        body = stream.read(_TOKEN_LIMIT + 1)
    if len(body) > _TOKEN_LIMIT:
        raise ValueError("token file is too large")
    try:
        token = body.decode("utf-8").strip()
    except UnicodeError:
        raise ValueError("token file must contain UTF-8 text") from None
    if not token:
        raise ValueError("token file is empty")
    if any(char in token for char in "\r\n\x00"):
        raise ValueError("token file must contain one token on a single line")
    return token


def _confirm(name):
    try:
        answer = input(f"Remove agent {name!r}, its configuration and wrapper? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _definition(path):
    from .composition import compose, read_document

    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError(f"agent source must be a regular file: {path}")
    tree = compose(read_document(path.resolve()), kinds={"agent"})
    if "agent" not in tree or tree["agent"].kind != "mapping":
        raise ValueError("an agent source must contain an agent mapping")
    return tree


def _stored_config(store, name):
    path = store.locate(name) / "agent.cfg"
    if not path.is_file():
        raise ValueError(f"stored agent.cfg must be a regular file: {path}")
    return path


def _source_name(tree):
    branch = tree["agent"]
    if "name" not in branch or branch["name"].kind != "scalar" or branch["name"].type != "str":
        raise ValueError("source needs a literal agent.name, or choose one with --name")
    return branch["name"].value


def _error_text(error, token=None):
    # UnicodeError can quote raw input; OS failures need only their reason/path.
    if isinstance(error, OSError):
        message = error.strerror or str(error) or "filesystem operation failed"
        if error.filename is not None:
            message += f": {error.filename}"
    elif isinstance(error, UnicodeError):
        message = "configuration is not valid UTF-8 text"
    else:
        message = str(error)
    return message.replace(token, "[redacted]") if token else message


def _run(args, parser, token):
    import yapyon
    from .composition import read_document, read_text
    from .identifiers import validate_id
    from .store import AgentStore, make_agent

    # Validate every identifier before constructing a store or touching its registry.
    if args.name is not None:
        validate_id(args.name)
    if args.get_secret is not None:
        validate_id(args.get_secret)
    if args.create and len(args.selection) == 2:
        validate_id(args.selection[0], kind="persona")
        validate_id(args.selection[1], kind="harness")
    elif args.selection and not args.create:
        validate_id(args.selection[0])

    store = AgentStore(root=Path(args.store).expanduser() if args.store is not None else None)
    if args.setup:
        from .installation import setup_store

        setup_store(store, backend=args.backend, secret_root=args.secret_root)
        print(f"Configured agent store {store.root}.")
        return 0

    if args.get_secret is not None:
        from .installation import fetch_secret

        manager = store.load(args.get_secret)["runtime"]["secret_backend"]
        if not manager:
            raise ValueError(f"Agent {args.get_secret!r} is keyless; no credential is configured.")
        print(fetch_secret(manager))
        return 0

    if not args.selection:
        agents = store.agents()
        if agents:
            print("Registered agents:")
            for name, path in sorted(agents.items()):
                print(f"  {name}\t{path}")
        else:
            print("No registered agents.")
        print()
        parser.print_help()
        return 0

    if args.remove:
        from .installation import remove_agent

        name = args.selection[0]
        store.locate(name)
        if not args.yes and not _confirm(name):
            print("Cancelled; nothing removed.")
            return 0
        remove_agent(store, name, confirm=True)
        return 0

    if args.export is not None:
        from .composition import write_document

        source = _stored_config(store, args.selection[0])
        destination = Path(args.export).expanduser()
        if source.resolve() == destination.resolve():
            raise ValueError("export destination is the installed agent.cfg; choose another file")
        write_document(read_document(source), destination, overwrite=args.force)
        print(f"Exported agent {args.selection[0]!r} to {destination}.")
        return 0

    if not (args.create or args.update):
        source = _stored_config(store, args.selection[0])
        print(read_text(source), end="")
        return 0

    if args.create:
        if len(args.selection) == 2:
            persona, harness = args.selection
            name = args.name if args.name is not None else f"{persona}_{harness}"
            validate_id(name)
            definition = make_agent(persona, harness, name=name)
        else:
            definition = _definition(args.selection[0])
            name = args.name if args.name is not None else _source_name(definition)
            validate_id(name)
    else:
        name = args.selection[0]
        stored = _stored_config(store, name)
        if args.name is not None and args.name != name and args.name in store.agents():
            raise ValueError(f"agent {args.name!r} already exists; choose another --name")
        definition = (_definition(args.selection[1]) if len(args.selection) == 2
                      else read_document(stored))

    if args.no_token:
        definition.merge(yapyon.parse("agent:\n  secret:= None\n"))

    if args.create:
        candidate = store.prepare_create(name, definition,
                                         path=Path(args.path).expanduser() if args.path is not None else None)
    else:
        candidate = store.prepare_update(name, definition, new_name=args.name)

    from .installation import apply_installation, commit_credential, prepare_installation

    installation = prepare_installation(candidate.resolved, token=token, verify=args.verify,
                                        install_wrapper=not args.no_wrapper)
    commit_credential(installation)
    location = store.commit(candidate)
    name = candidate.name
    try:
        apply_installation(installation)
    except (OSError, UnicodeError, ValueError, yapyon.AkanError, yapyon.Yakamashiwa) as error:
        raise ValueError(
            f"Agent {name!r} configuration was saved at {location}, but installation failed: "
            f"{_error_text(error, token)}. The saved configuration was kept; retry with --update {name}."
        ) from None
    print(f"{'Created' if args.create else 'Updated'} agent {name!r} at {location}.")
    return 0


def main(argv=None):
    """Return an exit status without tracebacks for expected user/configuration errors."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        _validate_options(parser, args)
    except SystemExit as exit_request:
        return exit_request.code

    import yapyon

    token = None
    try:
        token = _read_token(args.token) if args.token is not None else None
        return _run(args, parser, token)
    except (OSError, UnicodeError, ValueError, yapyon.AkanError, yapyon.Yakamashiwa) as error:
        print(f"Error: {_error_text(error, token)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
