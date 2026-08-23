import pytest

from retailsense_contracts.config import StoreConfig
from retailsense_contracts.testing import sample_store_config


@pytest.fixture
def cfg() -> StoreConfig:
    return sample_store_config()
