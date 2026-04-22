"""Test models.yaml registry schema roundtrip and validation."""

from pathlib import Path
from sec_review_framework.config import ModelsConfig


def test_models_yaml_roundtrip():
    """Load models.yaml via ModelsConfig.from_yaml(); must parse."""
    config_path = Path("config/models.yaml")
    assert config_path.exists(), f"config/models.yaml not found at {config_path.resolve()}"

    config = ModelsConfig.from_yaml(config_path)
    assert config.providers is not None
    assert len(config.providers) > 0


def test_all_entries_have_display_name():
    """Assert every entry has a non-empty display_name."""
    config_path = Path("config/models.yaml")
    config = ModelsConfig.from_yaml(config_path)

    for model_id, provider in config.providers.items():
        assert provider.display_name, f"model {model_id} missing display_name"
        assert isinstance(provider.display_name, str)
        assert len(provider.display_name) > 0


def test_auth_requirements():
    """Assert every auth=api_key entry has api_key_env; every auth=aws entry has region."""
    config_path = Path("config/models.yaml")
    config = ModelsConfig.from_yaml(config_path)

    for model_id, provider in config.providers.items():
        if provider.auth == "api_key":
            assert provider.api_key_env, f"model {model_id}: api_key_env required when auth='api_key'"
            assert isinstance(provider.api_key_env, str)
        elif provider.auth == "aws":
            assert provider.region, f"model {model_id}: region required when auth='aws'"
            assert isinstance(provider.region, str)


def test_defaults_merged():
    """Assert defaults merged: every entry's temperature == 0.2 and max_tokens == 8192."""
    config_path = Path("config/models.yaml")
    config = ModelsConfig.from_yaml(config_path)

    for model_id, provider in config.providers.items():
        assert provider.temperature == 0.2, (
            f"model {model_id}: expected temperature 0.2, got {provider.temperature}"
        )
        assert provider.max_tokens == 8192, (
            f"model {model_id}: expected max_tokens 8192, got {provider.max_tokens}"
        )
