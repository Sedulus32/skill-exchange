"""Tests for the Rating system."""
import time

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def unique_email(name: str) -> str:
    return f"{name}-{int(time.time() * 1000)}@example.com"


def register_user(name: str, email: str, skills_teach: str, skills_learn: str) -> int:
    """Register a user via the form and return their user ID."""
    response = client.post(
        "/register",
        data={
            "name": name,
            "email": email,
            "password": "secret123",
            "skills_can_teach": skills_teach,
            "skills_want_to_learn": skills_learn,
            "bio": f"Bio for {name}",
        },
    )
    assert response.status_code == 200
    assert "Your profile has been created" in response.text
    # The user is auto-logged in; find their ID from the profile link in the navbar
    # The success page contains a link to /user/{id}
    import re

    match = re.search(r"/user/(\d+)", response.text)
    assert match, "Could not find user ID in registration response"
    return int(match.group(1))


def test_rating_flow():
    # --- Register two users ---
    email1 = unique_email("rater")
    email2 = unique_email("ratee")
    user1_id = register_user("Rater", email1, "Python", "FastAPI")
    user2_id = register_user("Ratee", email2, "FastAPI", "Python")

    # After registering user2, the session is logged in as user2.
    # Log out and log back in as user1 so we can test rating user2.
    client.get("/logout")
    response = client.post(
        "/login",
        data={"email": email1, "password": "secret123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/user/{user1_id}"

    # --- User 1 (Rater) views User 2's profile ---
    # Should see "No ratings yet" and the rating form
    response = client.get(f"/user/{user2_id}")
    assert response.status_code == 200
    assert "No ratings yet" in response.text
    assert "Rate Ratee" in response.text
    assert 'action="/user/{}/rate"'.format(user2_id) in response.text

    # --- User 1 submits a rating for User 2 ---
    response = client.post(
        f"/user/{user2_id}/rate",
        data={"score": "5", "review": "Great teacher!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/user/{user2_id}"

    # --- User 1 views User 2's profile again ---
    # Should now see the average rating, count, and their existing rating
    response = client.get(f"/user/{user2_id}")
    assert response.status_code == 200
    assert "5.0" in response.text
    assert "1 rating" in response.text
    assert "You rated Ratee 5/5" in response.text
    assert "Great teacher!" in response.text
    # The form should NOT be shown anymore
    assert 'action="/user/{}/rate"'.format(user2_id) not in response.text

    # --- User 1 tries to rate User 2 again (should be blocked) ---
    response = client.post(
        f"/user/{user2_id}/rate",
        data={"score": "1", "review": "Trying to re-rate"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/user/{user2_id}"

    # The rating should still be 5.0 with 1 rating
    response = client.get(f"/user/{user2_id}")
    assert "5.0" in response.text
    assert "1 rating" in response.text
    assert "Trying to re-rate" not in response.text

    # --- User 1 cannot rate themselves ---
    response = client.post(
        f"/user/{user1_id}/rate",
        data={"score": "5", "review": "Self rating"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/user/{user1_id}"

    # Their own profile should not show a rating form
    response = client.get(f"/user/{user1_id}")
    assert "No ratings yet" in response.text
    assert 'action="/user/{}/rate"'.format(user1_id) not in response.text

    # --- User 2 rates User 1 (reverse direction) ---
    # Log out and log in as user2
    client.get("/logout")
    response = client.post(
        "/login",
        data={"email": email2, "password": "secret123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/user/{user2_id}"

    response = client.post(
        f"/user/{user1_id}/rate",
        data={"score": "4", "review": "Very helpful!"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    # User 1's profile should now show 4.0 with 1 rating
    response = client.get(f"/user/{user1_id}")
    assert "4.0" in response.text
    assert "1 rating" in response.text

    # --- Not logged in: rating form should not be shown ---
    client.get("/logout")
    response = client.get(f"/user/{user2_id}")
    assert response.status_code == 200
    assert "Rate Ratee" not in response.text
    assert 'action="/user/{}/rate"'.format(user2_id) not in response.text

    # --- Not logged in: POST /rate should redirect to login ---
    response = client.post(
        f"/user/{user2_id}/rate",
        data={"score": "3", "review": "Anonymous"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    print("\nALL RATING TESTS PASSED")


if __name__ == "__main__":
    test_rating_flow()
    print("\nALL RATING TESTS PASSED")