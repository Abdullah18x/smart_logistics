"""Settings, and the guards that stop a bad configuration reaching a server."""

import pytest
from pydantic import ValidationError

from app.core.config import _DEFAULT_JWT_KEY, Settings

REQUIRED = {"_env_file": None}  # ignore the developer's .env while testing defaults


def build(**overrides) -> Settings:
    return Settings(**REQUIRED | overrides)


class TestSigningKeyGuard:
    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_a_deployed_environment_refuses_the_default_key(self, environment):
        """It is in the repository: anyone who has read the source could mint an admin token."""
        with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set"):
            build(environment=environment, jwt_secret_key=_DEFAULT_JWT_KEY)

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_a_real_key_is_accepted(self, environment):
        settings = build(environment=environment, jwt_secret_key="a" * 64)
        assert settings.jwt_secret_key == "a" * 64

    @pytest.mark.parametrize("environment", ["local", "test"])
    def test_development_still_runs_out_of_the_box(self, environment):
        """A fresh clone has to start without ceremony, so the guard stays quiet here."""
        settings = build(environment=environment, jwt_secret_key=_DEFAULT_JWT_KEY)
        assert settings.jwt_secret_key == _DEFAULT_JWT_KEY

    def test_the_error_says_how_to_generate_one(self):
        with pytest.raises(ValidationError, match="openssl rand"):
            build(environment="production", jwt_secret_key=_DEFAULT_JWT_KEY)


class TestEnvironment:
    def test_production_is_recognised(self):
        assert build(environment="production", jwt_secret_key="x" * 64).is_production is True

    @pytest.mark.parametrize("environment", ["local", "test", "staging"])
    def test_everything_else_is_not(self, environment):
        assert build(environment=environment, jwt_secret_key="x" * 64).is_production is False

    def test_an_unknown_environment_is_rejected(self):
        with pytest.raises(ValidationError):
            build(environment="prod")


class TestDefaults:
    def test_the_defaults_are_usable_for_local_development(self):
        settings = build()
        assert settings.access_token_ttl_minutes == 15
        assert settings.refresh_token_ttl_days == 14
        assert settings.max_failed_login_attempts == 5
        assert settings.database_url.startswith("postgresql+asyncpg://")

    def test_unknown_environment_variables_are_ignored(self):
        """Compose and Kubernetes inject variables this app does not read."""
        assert build(SOME_UNRELATED_VARIABLE="x").app_name == "SmartLogistics"
