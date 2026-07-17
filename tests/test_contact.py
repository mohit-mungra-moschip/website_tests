import requests


def test_contact_submission(base_url):

    payload = {
        "name": "John",
        "email": "john@test.com",
        "message": "Hello"
    }

    response = requests.post(
        f"{base_url}/submit",
        data=payload,
        allow_redirects=False
    )

    assert response.status_code == 302