import requests


def test_contacts_api(base_url):

    response = requests.get(f"{base_url}/contacts")

    assert response.status_code == 200

    assert isinstance(response.json(), list)