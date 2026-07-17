import pytest
import requests

BASE_URL = "http://localhost:5000"


@pytest.fixture
def base_url():
    return BASE_URL