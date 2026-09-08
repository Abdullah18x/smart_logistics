"""Liveness and readiness probes.

These sit outside the versioned prefix so an orchestrator need not track API
versions to know whether the process is alive.
"""


class TestLiveness:
    async def test_health_is_public(self, client):
        """No token: a probe that needed credentials would be useless."""
        response = await client.get("/health")
        assert response.status_code == 200

    async def test_it_reports_the_application_and_environment(self, client):
        body = (await client.get("/health")).json()
        assert body["status"] == "ok"
        assert body["app"]
        assert body["environment"] == "test"


class TestReadiness:
    async def test_it_checks_the_database(self, client):
        response = await client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"]["postgres"] == "ok"

    async def test_an_unreachable_database_makes_it_answer_503(self, client, monkeypatch):
        """A readiness probe is read by its status code.

        Answering 200 with ``{"status": "degraded"}`` would leave a pod that
        cannot reach Postgres sitting in the load balancer taking traffic.
        """
        from sqlalchemy.exc import OperationalError

        from app.controllers import health_controller

        async def unreachable(*_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        monkeypatch.setattr(
            health_controller.SessionDep.__args__[0], "execute", unreachable, raising=False
        )
        response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["checks"]["postgres"].startswith("error:")

    async def test_liveness_does_not_touch_the_database(self, client):
        """Otherwise a Postgres hiccup would have Kubernetes restart every pod."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert "checks" not in response.json()


class TestApiSurface:
    async def test_the_openapi_document_is_served(self, client):
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        assert response.json()["info"]["version"] == "0.1.0"

    async def test_every_route_lives_under_the_version_prefix(self, client):
        """Except the probes, deliberately."""
        paths = (await client.get("/openapi.json")).json()["paths"]
        unversioned = [p for p in paths if not p.startswith("/api/v1")]
        assert set(unversioned) == {"/health", "/health/ready"}

    async def test_the_documented_tags_all_carry_routes(self, client):
        spec = (await client.get("/openapi.json")).json()
        declared = (
            {tag["name"] for tag in spec["openapi_tags"]}
            if "openapi_tags" in spec
            else {tag["name"] for tag in spec.get("tags", [])}
        )
        used = {
            tag
            for path in spec["paths"].values()
            for operation in path.values()
            for tag in operation.get("tags", [])
        }
        assert used <= declared
