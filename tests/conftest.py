import pytest
from unittest.mock import MagicMock
import sys
import os

# Add repo root to path so we can import 'skills' and 'skill-assay'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(autouse=True)
def isolate_skill_assay_config(monkeypatch, tmp_path):
    """
    Point global config at an empty temp directory for every test.

    Without this, a developer's real ~/.config/skill-assay/config.yaml (for example
    after ``skill-assay mail signature init``) switches discovery to configured
    mode and breaks legacy-order assertions in discovery/loader tests.
    """
    from skill_assay.core.config import GLOBAL_CONFIG_DIR_ENV, clear_config_cache

    isolated = tmp_path / "skill-assay-global-config"
    isolated.mkdir()
    monkeypatch.setenv(GLOBAL_CONFIG_DIR_ENV, str(isolated))
    clear_config_cache()
    yield
    clear_config_cache()


@pytest.fixture
def mock_anthropic():
    """Mocks the Anthropic client."""
    mock_client = MagicMock()
    # Mock the messages.create return structure
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='{"field_id": "value"}')]
    return mock_client


@pytest.fixture
def mock_skill_loader():
    """Mocks the SkillLoader to return a sample skill bundle."""
    # This might not be needed if we import the class directly, but good to have.
    return MagicMock()
