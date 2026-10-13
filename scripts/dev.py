"""SmartLogistics developer CLI — one tool for macOS, Linux and Windows.

Run it through the wrappers in the repo root, which make sure the workspace
virtualenv exists first:

    ./dev.sh <command>        macOS / Linux
    .\\dev.ps1 <command>      Windows PowerShell
    dev <command>             Windows cmd.exe (dev.cmd)

Local development (services on your machine, infrastructure in Docker):

    setup                     install the workspace and generate the signing key
    infra up|down|status      start/stop the 5 databases, Kafka, Temporal (UI on
                              :8233), MongoDB, Jaeger and the gateway
                              (http://localhost:8000, Swagger UI at /docs)
    migrate [svc ...]         apply migrations (all SQL services by default)
    migrate --down [svc ...]  roll every migration back
    seed                      load development data (warehouse, identity, inventory, courier)
    start [svc ...]           run the APIs and their workers in one terminal
                              --no-workers, --reload; Ctrl+C stops everything
    status                    health of every service on 8001-8007
    docs                      regenerate docs/curl (OpenAPI, Postman, curl) and
                              docs/database.md from the code
    reset                     migrate --down, migrate, seed

Everything in Docker:

    docker up|down [-v]|ps|logs [svc]|migrate|seed

Quality:

    test [svc ...]            platform, service suites and cross-service e2e
    lint                      ruff, format check, service-boundary contracts
    check                     lint plus a migration drift check per service

Set SL_PG_HOST=host:port to put every database on one Postgres server instead
of the per-service instances Docker Compose publishes on 5441-5445.

Standard library only (plus `cryptography` for `keys`, already in the venv),
so it behaves the same on every OS and needs no shell features.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
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

#: name -> (API port, Postgres port published by Docker Compose, or None)
SERVICES: dict[str, tuple[int, int | None]] = {
    "identity": (8001, 5441),
    "warehouse": (8002, 5442),
    "inventory": (8003, 5443),
    "shipment": (8004, 5444),
    "courier": (8005, 5445),
    "orchestrator": (8006, None),
    "tracking": (8007, None),
}
#: Services with their own PostgreSQL database (and Alembic chain).
DB_SERVICES = tuple(name for name, (_, db_port) in SERVICES.items() if db_port)
#: Warehouse first: its events and seed ids are what the others line up with.
SEED_ORDER = ("warehouse", "identity", "inventory", "courier")
#: Services with a background worker (outbox relay and/or consumers).
WORKERS = tuple(SERVICES)
#: The full Docker stack's application containers (APIs and workers).
APP_CONTAINERS = (*SERVICES, *(f"{s}-worker" for s in WORKERS))
INFRA = (
    *(f"{name}-db" for name in DB_SERVICES),
    "kafka",
    "jaeger",
    "temporal-db",
    "temporal",
    "temporal-ui",
    "mongo",
    "docs",
    "gateway-local",
)
#: Development-only service-account credentials (Identity's SERVICE_CLIENTS).
DEV_SERVICE_CLIENTS = {"orchestrator": "orchestrator-dev-secret"}
#: Where uvicorn listens. The local gateway runs in Docker and reaches the APIs
#: through host.docker.internal: Docker Desktop (macOS, Windows) forwards that
#: to the host's loopback, but on Linux it is the bridge address, so the APIs
#: must listen on every interface there. Override with SL_BIND_HOST.
BIND_HOST = os.environ.get("SL_BIND_HOST") or (
    "0.0.0.0" if sys.platform.startswith("linux") else "127.0.0.1"
)
KEY_FILE = ROOT / "infra" / "keys" / "jwt-private.pem"
COLORS = {
    "identity": 36,
    "warehouse": 33,
    "inventory": 35,
    "shipment": 32,
    "courier": 34,
    "orchestrator": 91,
    "tracking": 96,
}


# --- helpers -------------------------------------------------------------------------


def say(message: str) -> None:
    print(f"\033[1m==> {message}\033[0m", flush=True)


def fail(message: str, code: int = 1) -> None:
    print(f"\033[31merror:\033[0m {message}", file=sys.stderr)
    sys.exit(code)


def pick(services: list[str] | None, among: tuple[str, ...] | None = None) -> list[str]:
    among = among or tuple(SERVICES)
    chosen = services or list(among)
    unknown = [s for s in chosen if s not in among]
    if unknown:
        fail(f"unknown service(s): {', '.join(unknown)}. Choose from: {', '.join(among)}")
    return chosen


def db_host(service: str) -> str:
    return os.environ.get("SL_PG_HOST") or f"localhost:{SERVICES[service][1]}"


def database_url(service: str) -> str:
    return f"postgresql+asyncpg://{service}:{service}@{db_host(service)}/{service}"


def api_url(service: str) -> str:
    return f"http://localhost:{SERVICES[service][0]}"


def service_env(service: str) -> dict[str, str]:
    """The environment a service needs when it runs on the host, not in Docker."""
    env = {
        **os.environ,
        "ENVIRONMENT": os.environ.get("ENVIRONMENT", "local"),
        "LOG_JSON": "false",
        "PYTHONUNBUFFERED": "1",
        "KAFKA_BOOTSTRAP_SERVERS": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092"),
        "JWKS_URL": f"{api_url('identity')}/.well-known/jwks.json",
        "TEMPORAL_ADDRESS": os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        "MONGO_URL": os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
    }
    for other in SERVICES:
        env[f"{other.upper()}_SERVICE_URL"] = api_url(other)
    if SERVICES[service][1]:
        env["DATABASE_URL"] = database_url(service)
    if service == "identity":
        env["JWT_PRIVATE_KEY_FILE"] = str(KEY_FILE)
        env["SERVICE_CLIENTS"] = json.dumps(DEV_SERVICE_CLIENTS)
        env.pop("JWKS_URL")
    if service in DEV_SERVICE_CLIENTS:
        env["SERVICE_CLIENT_ID"] = service
        env["SERVICE_CLIENT_SECRET"] = DEV_SERVICE_CLIENTS[service]
    return env


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    """Run a command to completion; stop the CLI if it fails."""
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        fail(f"`{' '.join(cmd)}` failed (exit {result.returncode})", result.returncode)


def compose(*args: str, check: bool = True) -> None:
    if shutil.which("docker") is None:
        fail("Docker is not installed or not on PATH.")
    if check:
        run(["docker", "compose", *args])
    else:
        subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True)


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
        say("starting databases, Kafka, Jaeger and the gateway (services run on this machine)")
        # The full Docker stack's gateway, APIs and workers would collide with the
        # services you run here: same ports (8000-8007), and its workers would take
        # Kafka events meant for yours. With `restart: unless-stopped` they come back
        # after a Docker restart, so remove them every time.
        compose("rm", "-sf", "gateway", *APP_CONTAINERS, check=False)
        compose("up", "-d", "--wait", *INFRA)
        say("infrastructure ready — next: migrate, seed, start")
        say("then: gateway http://localhost:8000 · Swagger UI http://localhost:8000/docs")
    elif args.action == "down":
        compose("stop", *INFRA)
    else:
        compose("ps", *INFRA)


def cmd_migrate(args: argparse.Namespace) -> None:
    target = ["downgrade", "base"] if args.down else ["upgrade", "head"]
    for service in pick(args.services, DB_SERVICES):
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


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _port_free(host: str, port: int) -> bool:
    """Whether uvicorn could bind host:port (same SO_REUSEADDR setting it uses)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _listeners(port: int) -> list[tuple[str, str]]:
    """Every (pid, command) listening on a port, where the OS can tell us. Under
    --reload the reloader and its server both hold the socket, so there can be two."""
    if IS_WINDOWS or not shutil.which("lsof"):
        return []
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[tuple[str, str]] = []
    for line in out.splitlines():
        if line.startswith("p"):
            found.append((line[1:], ""))
        elif line.startswith("c") and found:
            found[-1] = (found[-1][0], line[1:])
    return found


def _containers_publishing(port: int) -> list[str]:
    """Running containers that publish a port on this machine (e.g. the full Docker
    stack's APIs on 8001-8007)."""
    if not shutil.which("docker"):
        return []
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return []
    return [name for name in out.split() if name]


def check_ports(services: list[str]) -> None:
    """Refuse to start anything when an API port is taken, and say who holds it:
    the Docker stack's containers, or a server left behind by an earlier run."""
    busy = [(s, SERVICES[s][0]) for s in services if not _port_free(BIND_HOST, SERVICES[s][0])]
    if not busy:
        return
    lines, pids, containers = [], [], []
    for service, port in busy:
        in_docker = _containers_publishing(port)
        holders = [] if in_docker else _listeners(port)
        if in_docker:
            who = f" by Docker container {', '.join(in_docker)}"
            containers += in_docker
        elif holders:
            who = " by " + ", ".join(f"PID {pid} ({cmd})" for pid, cmd in holders)
            pids += [pid for pid, _ in holders]
        else:
            who = ""
        lines.append(f"  {service} :{port} — in use{who}")
    hints = []
    if containers:
        hints.append(
            "The full Docker stack (./dev.sh docker up) is still running its APIs. Remove "
            "them and keep the infrastructure:  ./dev.sh infra up"
        )
    if pids:
        unique = " ".join(dict.fromkeys(pids))
        hints.append(
            f"Stop the leftover processes:  kill {unique}   (still there? kill -9 {unique})"
        )
    if not hints:
        hints.append(
            "Find it with:  netstat -ano | findstr :<port>   then  taskkill /PID <pid> /F"
            if IS_WINDOWS
            else "Find it with:  lsof -nP -iTCP:<port> -sTCP:LISTEN   then  kill <pid>"
        )
    fail(
        "these ports are already taken:\n"
        + "\n".join(lines)
        + "".join(f"\n       {hint}" for hint in hints)
        + "\n       or start only the others:  ./dev.sh start <service> ..."
    )


def check_infra(services: list[str], workers: bool) -> None:
    """Fail fast, with the fix, when the Docker infrastructure isn't running."""
    missing = []
    for service in (s for s in services if s in DB_SERVICES):
        host, _, port = db_host(service).rpartition(":")
        if not _port_open(host or "localhost", int(port)):
            missing.append(f"{service}-db (:{port})")
    kafka = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092").split(",")[0]
    kafka_host, _, kafka_port = kafka.rpartition(":")
    kafka_up = _port_open(kafka_host or "localhost", int(kafka_port))
    if missing:
        fail(
            f"can't reach {', '.join(missing)}"
            + ("" if kafka_up else f" or Kafka (:{kafka_port})")
            + ".\n       Start the infrastructure first:  ./dev.sh infra up"
        )
    for service, (name, port) in (
        ("orchestrator", ("Temporal", 7233)),
        ("tracking", ("MongoDB", 27017)),
    ):
        if service in services and not _port_open("localhost", port):
            print(
                f"\033[33mwarning:\033[0m {name} (:{port}) isn't reachable — {service} will "
                "wait for it (./dev.sh infra up)."
            )
    if workers and not kafka_up:
        print(
            f"\033[33mwarning:\033[0m Kafka (:{kafka_port}) isn't reachable — the workers "
            "will keep retrying until it is (./dev.sh infra up). Events wait in "
            "each service's outbox meanwhile."
        )


def cmd_start(args: argparse.Namespace) -> None:
    services = pick(args.services)
    check_ports(services)
    check_infra(services, workers=not args.no_workers)

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
        kwargs: dict = {}
        if IS_WINDOWS:
            # Own process group, so Ctrl+C can be forwarded to each child cleanly.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Own process group, so stopping it also stops what it spawned:
            # with --reload, uvicorn's reloader runs the server as a child, and
            # signalling only the reloader can leave that child holding the port.
            kwargs["start_new_session"] = True
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
        api = [
            PYTHON, "-m", "uvicorn", f"{service}.main:app",
            "--host", BIND_HOST, "--port", str(port),
        ]  # fmt: skip
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
    say("gateway http://localhost:8000 · Swagger UI for every service http://localhost:8000/docs")
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
            _signal_group(proc, signal.SIGTERM)
        deadline = time.time() + 10
        for _, proc in processes:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=max(0.1, deadline - time.time()))
        # Anything still alive in a group — the process or a child it spawned —
        # is killed, so no server outlives `start` and blocks the next one.
        for _, proc in processes:
            _signal_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    if IS_WINDOWS:
        if proc.poll() is None:
            if sig == signal.SIGTERM:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.kill()
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)  # the group id is the child's pid (new session)


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
        compose("rm", "-sf", "gateway-local", check=False)  # local mode's gateway owns :8000
        compose("up", "-d", "--build")
        say("stack starting — gateway http://localhost:8000, Swagger UI http://localhost:8000/docs")
        say("Jaeger http://localhost:16686")
    elif action == "down":
        compose("--profile", "local", "down", *(["-v"] if args.volumes else []))
    elif action == "ps":
        compose("ps")
    elif action == "logs":
        compose("logs", "-f", *([args.service] if args.service else []))
    elif action == "migrate":
        for service in DB_SERVICES:
            say(f"{service}: alembic upgrade head (in container)")
            compose("exec", service, "alembic", "-c", "/app/alembic.ini", "upgrade", "head")
    elif action == "seed":
        for service in SEED_ORDER:
            say(f"seeding {service} (in container)")
            compose("exec", service, "python", "-m", f"{service}.seed")


def cmd_docs(_: argparse.Namespace) -> None:
    say("regenerating docs/curl from every service's routes")
    run([PYTHON, str(ROOT / "scripts" / "generate_api_docs.py")])
    say("regenerating docs/database.md from every service's models")
    run([PYTHON, str(ROOT / "scripts" / "generate_db_docs.py")])


def cmd_test(args: argparse.Namespace) -> None:
    services = pick(args.services)
    if not args.services:
        say("sl-platform unit tests")
        run([PYTHON, "-m", "pytest", "tests"], cwd=ROOT / "libs" / "sl-platform")
    for service in services:
        say(f"{service} tests")
        env = {**os.environ}
        if service in DB_SERVICES:
            env["DATABASE_URL"] = database_url(service)
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
    for service in DB_SERVICES:
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

    infra = sub.add_parser(
        "infra", help="databases, Kafka, Temporal, MongoDB, Jaeger and the gateway in Docker"
    )
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
    sub.add_parser("docs", help="regenerate docs/curl and docs/database.md").set_defaults(
        func=cmd_docs
    )

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
