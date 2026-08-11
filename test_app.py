import time

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def unique_email(name: str) -> str:
    """Generate a unique email so tests can be re-run without conflicts."""
    return f"{name}-{int(time.time() * 1000)}@example.com"


def test_create_and_get_users():
    # Create a user
    response = client.post("/users/", json={
        "name": "Alice",
        "email": unique_email("alice"),
        "skills_can_teach": ["Python", "SQL"],
        "skills_want_to_learn": ["FastAPI", "Docker"],
        "bio": "I love teaching Python!"
    })
    print("Create status:", response.status_code)
    print("Create body:", response.json())
    assert response.status_code == 200

    # Get all users
    response = client.get("/users/")
    print("Get status:", response.status_code)
    print("Get body:", response.json())
    assert response.status_code == 200
    assert response.json()[0]["skills_can_teach"] == ["Python", "SQL"]

    print("\nALL TESTS PASSED")


def test_match_endpoint():
    # Alice: teaches Python/SQL, wants to learn FastAPI/Docker
    alice = client.post("/users/", json={
        "name": "Alice",
        "email": unique_email("alice"),
        "skills_can_teach": ["Python", "SQL"],
        "skills_want_to_learn": ["FastAPI", "Docker"],
        "bio": "I love teaching Python!"
    }).json()

    # Bob: teaches FastAPI, wants to learn Python  -> mutual match with Alice
    bob = client.post("/users/", json={
        "name": "Bob",
        "email": unique_email("bob"),
        "skills_can_teach": ["FastAPI"],
        "skills_want_to_learn": ["Python"],
        "bio": "FastAPI enthusiast"
    }).json()

    # Carol: teaches Docker, wants to learn SQL  -> mutual match with Alice
    carol = client.post("/users/", json={
        "name": "Carol",
        "email": unique_email("carol"),
        "skills_can_teach": ["Docker"],
        "skills_want_to_learn": ["SQL"],
        "bio": "Container expert"
    }).json()

    # Dave: teaches FastAPI but wants to learn Go (Alice doesn't teach Go)
    #       -> NOT a match (only one-way: Bob teaches Alice, but not vice versa)
    dave = client.post("/users/", json={
        "name": "Dave",
        "email": unique_email("dave"),
        "skills_can_teach": ["FastAPI"],
        "skills_want_to_learn": ["Go"],
        "bio": "Go learner"
    }).json()

    # Eva: teaches Rust, wants to learn Rust too -> no overlap with Alice
    eva = client.post("/users/", json={
        "name": "Eva",
        "email": unique_email("eva"),
        "skills_can_teach": ["Rust"],
        "skills_want_to_learn": ["Rust"],
        "bio": "Rust fan"
    }).json()

    # Get matches for Alice (user_id = alice["id"])
    response = client.get(f"/match/{alice['id']}")
    print("Match status:", response.status_code)
    print("Match body:", response.json())
    assert response.status_code == 200

    matched_ids = [user["id"] for user in response.json()]
    assert bob["id"] in matched_ids
    assert carol["id"] in matched_ids
    assert dave["id"] not in matched_ids
    assert eva["id"] not in matched_ids

    # Matched user response should include all required fields
    for user in response.json():
        assert "name" in user
        assert "email" in user
        assert "skills_can_teach" in user
        assert "skills_want_to_learn" in user
        assert "bio" in user

    # Non-existent user should return 404
    response = client.get("/match/999999")
    print("404 status:", response.status_code)
    assert response.status_code == 404

    print("\nMATCH TESTS PASSED")


if __name__ == "__main__":
    test_create_and_get_users()
    test_match_endpoint()
