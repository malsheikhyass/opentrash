import argparse
import importlib
from importlib.metadata import PackageNotFoundError, version

CORE_MODULES = [
    "opentrash.core.crs",
    "opentrash.core.duckdb_session",
    "opentrash.core.vehicle_ids",
    "opentrash.prep.sites",
    "opentrash.prep.static_layers",
    "opentrash.engine.enrichment",
    "opentrash.engine.segments",
    "opentrash.patterns.runner",
    "opentrash.routeview.runner",
    "opentrash.tonnage.pipeline",
]

OPTIONAL_DEPENDENCIES = {
    "geo": ["geopandas", "shapely", "pyarrow", "duckdb"],
    "geotab": ["mygeotab", "pytz"],
    "postgres": ["psycopg2", "sqlalchemy", "pytz"],
}


def package_version() -> str:
    """Return the installed OpenTrash package version."""
    try:
        return version("opentrash")
    except PackageNotFoundError:
        return "unknown"


def check_import(module_name: str) -> tuple[bool, str]:
    """Try importing a module and return a success flag plus message."""
    try:
        importlib.import_module(module_name)
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def cmd_version(_args: argparse.Namespace) -> int:
    """Print the installed package version."""
    print(package_version())
    return 0


def cmd_modules(_args: argparse.Namespace) -> int:
    """List core OpenTrash modules."""
    print("OpenTrash modules:")
    for module_name in CORE_MODULES:
        print(f"  - {module_name}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check core imports and optional dependency groups."""
    print(f"OpenTrash {package_version()}")
    print()

    failures = 0

    print("Core package modules:")
    for module_name in CORE_MODULES:
        ok, message = check_import(module_name)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {module_name}")
        if not ok:
            print(f"         {message}")
            failures += 1

    print()
    print("Optional dependency groups:")
    for group_name, deps in OPTIONAL_DEPENDENCIES.items():
        print(f"  {group_name}:")
        for dep in deps:
            ok, message = check_import(dep)
            status = "OK" if ok else "missing"
            print(f"    [{status}] {dep}")
            if args.strict and not ok:
                print(f"             {message}")
                failures += 1

    if failures:
        print()
        print(f"Doctor found {failures} issue(s).")
        return 1

    print()
    print("Doctor checks passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the OpenTrash CLI parser."""
    parser = argparse.ArgumentParser(
        prog="opentrash",
        description=(
            "OpenTrash command-line interface. "
            "Current CLI is intentionally lightweight; workflow commands are coming soon."
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"opentrash {package_version()}",
    )

    subparsers = parser.add_subparsers(dest="command")

    version_parser = subparsers.add_parser(
        "version",
        help="Print the installed OpenTrash version.",
    )
    version_parser.set_defaults(func=cmd_version)

    modules_parser = subparsers.add_parser(
        "modules",
        help="List core OpenTrash modules included in the package.",
    )
    modules_parser.set_defaults(func=cmd_modules)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check whether OpenTrash modules and optional dependencies import correctly.",
    )
    doctor_parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a failing exit code if optional dependencies are missing.",
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the OpenTrash CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())