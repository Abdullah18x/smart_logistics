"""SmartLogistics developer CLI — one tool for macOS, Linux and Windows.

Run it through the wrappers in the repo root, which make sure the workspace
virtualenv exists first:

    ./dev.sh <command>        macOS / Linux
    .\\dev.ps1 <command>      Windows PowerShell
    dev <command>             Windows cmd.exe (dev.cmd)

Local development (services on your machine, infrastructure in Docker):

    setup                     install the workspace and generate the signing key
    infra up|down|status      start/stop only the 4 databases, Kafka and Jaeger
    migrate [svc ...]         apply migrations (all services by default)
    migrate --down [svc ...]  roll every migration back
    seed                      load development data (warehouse, identity, inventory)
    start [svc ...]           run the APIs and their workers in one terminal
                              --no-workers, --reload; Ctrl+C stops everything
    status                    health of every service on 8001-8004
    reset                     migrate --down, migrate, seed

Everything in Docker:

    docker up|down [-v]|ps|logs [svc]|migrate|seed

Quality:

    test [svc ...]            platform, service suites and cross-service e2e
    lint                      ruff, format check, service-boundary contracts
    check                     lint plus a migration drift check per service

Set SL_PG_HOST=host:port to put every database on one Postgres server instead
of the per-service instances Docker Compose publishes on 5441-5444.

Standard library only (plus `cryptography` for `keys`, already in the venv),
so it behaves the same on every OS and needs no shell features.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
IS_WINDOWS = os.name == "nt"

#: name -> (API port, Postgres port published by Docker Compose)
SERVICES: dict[str, tuple[int, int]] = {
    "identity": (8001, 5441),
    "warehouse": (8002, 5442),
    "inventory": (8003, 5443),
    "shipment": (8004, 5444),
}
#: Warehouse first: its events and seed ids are what the others line up with.
SEED_ORDER = ("warehouse", "identity", "inventory")
#: Services with a background worker (outbox relay and/or consumers).
WORKERS = ("identity", "warehouse", "inventory", "shipment")
INFRA = ("identity-db", "warehouse-db", "inventory-db", "shipment-db", "kafka", "jaeger")
KEY_FILE = ROOT / "infra" / "keys" / "jwt-private.pem"
COLORS = {"identity": 36, "warehouse": 33, "inventory": 35, "shipment": 32}


# --- helpers -------------------------------------------------------------------------


def say(message: str) -> None:
    print(f"\033[1m==> {message}\033[0m", flush=True)


def fail(message: str, code: int = 1) -> None:
    print(f"\033[31merror:\033[0m {message}", file=sys.stderr)
    sys.exit(code)


def pick(services: list[str] | None) -> list[str]:
    chosen = services or list(SERVICES)
    unknown = [s for s in chosen if s not in SERVICES]
    if unknown:
        fail(f"unknown service(s): {', '.join(unknown)}. Choose from: {', '.join(SERVICES)}")
    return chosen


def database_url(service: str) -> str:
    host = os.environ.get("SL_PG_HOST") or f"localhost:{SERVICES[service][1]}"
    return f"postgresql+asyncpg://{service}:{service}@{host}/{service}"


def service_env(service: str) -> dict[str, str]:
    """The environment a service needs when it runs on the host, not in Docker."""
    env = {
        **os.environ,
        "ENVIRONMENT": os.environ.get("ENVIRONMENT", "local"),
        "LOG_JSON": "false",
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": database_url(service),
        "KAFKA_BOOTSTRAP_SERVERS": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092"),
        "JWKS_URL": f"http://localhost:{SERVICES['identity'][0]}/.well-known/jwks.json",
        "WAREHOUSE_SERVICE_URL": f"http://localhost:{SERVICES['warehouse'][0]}",
        "INVENTORY_SERVICE_URL": f"http://localhost:{SERVICES['inventory'][0]}",
    }
    if service == "identity":
        env["JWT_PRIVATE_KEY_FILE"] = str(KEY_FILE)
        env.pop("JWKS_URL")
    return env


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    """Run a command to completion; stop the CLI if it fails."""
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        fail(f"`{' '.join(cmd)}` failed (exit {result.returncode})", result.returncode)


def compose(*args: str) -> None:
    if shutil.which("docker") is None:
        fail("Docker is not installed or not on PATH.")
    run(["docker", "compose", *args])


# --- commands ------------------------------------------------------------------------


def cmd_keys(_: argparse.Namespace) -> None:
    if KEY_FILE.exists():
        say(f"signing key already exists: {KEY_FILE.relative_to(ROOT)}")
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    say(f"generated {KEY_FILE.relative_to(ROOT)} (git-ignored)")


def cmd_setup(args: argparse.Namespace) -> None:
    say("installing the workspace (every service + dev tools)")
    run(["uv", "sync", "--all-packages"])
    cmd_keys(args)


def cmd_infra(args: argparse.Namespace) -> None:
    if args.action == "up":
        say("starting databases, Kafka and Jaeger (services run on this machine)")
        compose("up", "-d", "--wait", *INFRA)
        say("infrastructure ready — next: migrate, seed, start")
    elif args.action == "down":
        compose("stop", *INFRA)
    else:
        compose("ps", *INFRA)


def cmd_migrate(args: argparse.Namespace) -> None:
    target = ["downgrade", "base"] if args.down else ["upgrade", "head"]
    for service in pick(args.services):
        say(f"{service}: alembic {' '.join(target)}")
        run(
            [PYTHON, "-m", "alembic", *target],
            cwd=ROOT / "services" / service,
            env=service_env(service),
        )


def cmd_seed(_: argparse.Namespace) -> None:
    for service in SEED_ORDER:
        say(f"seeding {service}")
        run(
            [PYTHON, "-m", f"{service}.seed"],
            cwd=ROOT / "services" / service,
            env=service_env(service),
        )
    say("seeded. Log in as admin@transfleet.com / SmartLogistics!2026")


def cmd_reset(args: argparse.Namespace) -> None:
    args.services, args.down = None, True
    cmd_migrate(args)
    args.down = False
    cmd_migrate(args)
    cmd_seed(args)


def _pump(name: str, stream, color: int) -> None:
    prefix = f"\033[{color}m{name:<18}|\033[0m "
    for line in iter(stream.readline, ""):
        sys.stdout.write(prefix + line)
        sys.stdout.flush()


def cmd_start(args: argparse.Namespace) -> None:
    services = pick(args.services)

    def interrupt(*_: object) -> None:
        raise KeyboardInterrupt

    # Explicit handlers: Ctrl+C in a terminal, but also a SIGTERM from an IDE or
    # a launcher that started us with SIGINT ignored, all stop every child.
    signal.signal(signal.SIGINT, interrupt)
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, interrupt)
    if "identity" in services and not KEY_FILE.exists():
        cmd_keys(args)
    processes: list[tuple[str, subprocess.Popen]] = []

    def launch(name: str, cmd: list[str], service: str) -> None:
        kwargs = {}
        if IS_WINDOWS:
            # Own process group, so Ctrl+C can be forwarded to each child cleanly.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT / "services" / service,
            env=service_env(service),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        processes.append((name, proc))
        threading.Thread(
            target=_pump, args=(name, proc.stdout, COLORS[service]), daemon=True
        ).start()

    # Identity first, so the others find the JWKS endpoint when they warm up.
    for service in sorted(services, key=lambda s: s != "identity"):
        port = SERVICES[service][0]
        api = [PYTHON, "-m", "uvicorn", f"{service}.main:app", "--port", str(port)]
        if args.reload:
            api += [
                "--reload",
                "--reload-dir",
                str(ROOT / "services" / service / "src"),
                "--reload-dir",
                str(ROOT / "libs" / "sl-platform" / "src"),
            ]
        launch(service, api, service)
        if service == "identity":
            time.sleep(1.5)
        if not args.no_workers and service in WORKERS:
            launch(f"{service}-worker", [PYTHON, "-m", f"{service}.worker"], service)

    say(
        "running: "
        + ", ".join(f"{s} :{SERVICES[s][0]}" for s in services)
        + (" (+ workers)" if not args.no_workers else "")
        + " — Ctrl+C to stop"
    )
    try:
        while True:
            for name, proc in processes:
                if proc.poll() is not None:
                    raise RuntimeError(f"{name} exited with code {proc.returncode}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        say("stopping")
    except RuntimeError as exc:
        print(f"\033[31m{exc}\033[0m — stopping the rest")
    finally:
        for _, proc in processes:
            if proc.poll() is None:
                if IS_WINDOWS:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
        deadline = time.time() + 10
        for _, proc in processes:
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()


def cmd_status(_: argparse.Namespace) -> None:
    for service, (port, _) in SERVICES.items():
        url = f"http://localhost:{port}/health/ready"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                state = f"\033[32mready\033[0m ({response.status})"
        except urllib.error.HTTPError as exc:
            state = f"\033[33mdegraded\033[0m ({exc.code})"
        except OSError:
            state = "\033[31mdown\033[0m"
        print(f"  {service:<10} :{port}  {state}")


def cmd_docker(args: argparse.Namespace) -> None:
    action = args.action
    if action == "up":
        cmd_keys(args)
        compose("up", "-d", "--build")
        say("stack starting — gateway http://localhost:8000, Jaeger http://localhost:16686")
    elif action == "down":
        compose("down", *(["-v"] if args.volumes else []))
    elif action == "ps":
        compose("ps")
    elif action == "logs":
        compose("logs", "-f", *([args.service] if args.service else []))
    elif action == "migrate":
        for service in SERVICES:
            say(f"{service}: alembic upgrade head (in container)")
            compose("exec", service, "alembic", "-c", "/app/alembic.ini", "upgrade", "head")
    elif action == "seed":
        for service in SEED_ORDER:
            say(f"seeding {service} (in container)")
            compose("exec", service, "python", "-m", f"{service}.seed")


def cmd_test(args: argparse.Namespace) -> None:
    services = pick(args.services)
    if not args.services:
        say("sl-platform unit tests")
        run([PYTHON, "-m", "pytest", "tests"], cwd=ROOT / "libs" / "sl-platform")
    for service in services:
        say(f"{service} tests")
        env = {**os.environ, "DATABASE_URL": database_url(service)}
        run([PYTHON, "-m", "pytest"], cwd=ROOT / "services" / service, env=env)
    if not args.services:
        say("cross-service e2e")
        run([PYTHON, "-m", "pytest", "tests/e2e"])


def cmd_lint(_: argparse.Namespace) -> None:
    run([PYTHON, "-m", "ruff", "check", "libs", "services", "tests", "scripts"])
    run([PYTHON, "-m", "ruff", "format", "--check", "libs", "services", "tests", "scripts"])
    lint_imports = Path(PYTHON).with_name("lint-imports.exe" if IS_WINDOWS else "lint-imports")
    run([str(lint_imports)])


def cmd_check(args: argparse.Namespace) -> None:
    cmd_lint(args)
    for service in SERVICES:
        say(f"{service}: alembic check")
        run(
            [PYTHON, "-m", "alembic", "check"],
            cwd=ROOT / "services" / service,
            env=service_env(service),
        )


# --- entry point ----------------------------------------------------------------------


def main() -> None:
    if IS_WINDOWS:
        os.system("")  # turn on ANSI colours in the Windows console
    parser = argparse.ArgumentParser(
        prog="dev", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    sub.add_parser("setup", help="install the workspace and generate the signing key").set_defaults(
        func=cmd_setup
    )
    sub.add_parser("keys", help="generate Identity's signing key").set_defaults(func=cmd_keys)

    infra = sub.add_parser("infra", help="databases, Kafka and Jaeger in Docker")
    infra.add_argument("action", choices=["up", "down", "status"])
    infra.set_defaults(func=cmd_infra)

    migrate = sub.add_parser("migrate", help="apply migrations to local databases")
    migrate.add_argument("services", nargs="*")
    migrate.add_argument("--down", action="store_true", help="roll everything back")
    migrate.set_defaults(func=cmd_migrate)

    sub.add_parser("seed", help="load development data").set_defaults(func=cmd_seed)
    sub.add_parser("reset", help="roll back, migrate and seed every service").set_defaults(
        func=cmd_reset
    )

    start = sub.add_parser("start", help="run services locally in one terminal")
    start.add_argument("services", nargs="*")
    start.add_argument("--no-workers", action="store_true", help="APIs only")
    start.add_argument("--reload", action="store_true", help="restart on code changes")
    start.set_defaults(func=cmd_start)

    sub.add_parser("status", help="health of every service").set_defaults(func=cmd_status)

    docker = sub.add_parser("docker", help="the full stack in Docker")
    docker.add_argument("action", choices=["up", "down", "ps", "logs", "migrate", "seed"])
    docker.add_argument("service", nargs="?", help="for logs: one service")
    docker.add_argument("-v", "--volumes", action="store_true", help="for down: delete data")
    docker.set_defaults(func=cmd_docker)

    test = sub.add_parser("test", help="run the test suites")
    test.add_argument("services", nargs="*")
    test.set_defaults(func=cmd_test)

    sub.add_parser("lint", help="ruff and service-boundary contracts").set_defaults(func=cmd_lint)
    sub.add_parser("check", help="lint plus migration drift").set_defaults(func=cmd_check)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
