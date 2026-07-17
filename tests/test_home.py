import requests


def test_home_page(base_url):

    response = requests.get(base_url)

    assert response.status_code == 200

    assert "Welcome" in response.text

    assert "Contact Us" in response.text