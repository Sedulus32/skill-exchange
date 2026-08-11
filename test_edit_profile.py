import time

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def unique_email(name: str) -> str:
    """Generate a unique email so tests can be re-run without conflicts."""
    return f"{name}-{int(time.time() * 1000)}@example.com"


def test_edit_profile_requires_login():
    """GET /edit-profile should redirect to /login when not logged in."""
    response = client.get("/edit-profile", follow_redirects=False)
    print("Not-logged-in status:", response.status_code)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_edit_profile_get_prefilled():
    """GET /edit-profile should show the form pre-filled with user data."""
    # Register a user via the form (auto-logs in)
    email = unique_email("edituser")
    response = client.post(
        "/register",
        data={
            "name": "Edit User",
            "email": email,
            "password": "secret123",
            "skills_can_teach": "Python, SQL",
            "skills_want_to_learn": "FastAPI, Docker",
            "bio": "Original bio",
        },
        follow_redirects=False,
    )
    print("Register status:", response.status_code)
    assert response.status_code == 200

    # Now GET the edit profile page (should be logged in from registration)
    response = client.get("/edit-profile")
    print("Edit profile GET status:", response.status_code)
    assert response.status_code == 200
    html = response.text
    assert "Edit User" in html
    assert email in html
    assert "Python, SQL" in html
    assert "FastAPI, Docker" in html
    assert "Original bio" in html
    # Email field should be disabled
    assert 'disabled' in html


def test_edit_profile_post_updates():
    """POST /edit-profile should update user data and show success."""
    # Register a user
    email = unique_email("edituser2")
    client.post(
        "/register",
        data={
            "name": "Edit User 2",
            "email": email,
            "password": "secret123",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "Docker",
            "bio": "Old bio",
        },
        follow_redirects=False,
    )

    # Update the profile
    response = client.post(
        "/edit-profile",
        data={
            "name": "Updated Name",
            "password": "",  # no new password
            "skills_can_teach": "Python, FastAPI",
            "skills_want_to_learn": "Docker, Kubernetes",
            "bio": "New bio",
        },
    )
    print("Edit profile POST status:", response.status_code)
    assert response.status_code == 200
    assert "Success" in response.text

    # Verify the user page shows updated data
    # Get the user's ID from the session
    user_id = client.cookies.get("session")
    # Find the user by checking the users page
    response = client.get("/users-page")
    assert response.status_code == 200
    assert "Updated Name" in response.text


def test_edit_profile_post_with_new_password():
    """POST /edit-profile with a new password should update the password."""
    # Register a user
    email = unique_email("edituser3")
    client.post(
        "/register",
        data={
            "name": "Password User",
            "email": email,
            "password": "oldpass123",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "Docker",
            "bio": "Bio",
        },
        follow_redirects=False,
    )

    # Update with a new password
    response = client.post(
        "/edit-profile",
        data={
            "name": "Password User",
            "password": "newpass456",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "Docker",
            "bio": "Bio",
        },
    )
    assert response.status_code == 200
    assert "Success" in response.text

    # Logout
    client.get("/logout", follow_redirects=False)

    # Try logging in with the new password
    response = client.post(
        "/login",
        data={"email": email, "password": "newpass456"},
        follow_redirects=False,
    )
    print("Login with new password status:", response.status_code)
    assert response.status_code == 303  # redirect to profile = success

    # Logout again
    client.get("/logout", follow_redirects=False)

    # Old password should no longer work
    response = client.post(
        "/login",
        data={"email": email, "password": "oldpass123"},
        follow_redirects=False,
    )
    print("Login with old password status:", response.status_code)
    assert response.status_code == 200  # stays on login page = failure


def test_edit_profile_validation():
    """POST /edit-profile with invalid data should show errors."""
    # Register a user
    email = unique_email("edituser4")
    client.post(
        "/register",
        data={
            "name": "Validation User",
            "email": email,
            "password": "secret123",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "Docker",
            "bio": "Bio",
        },
        follow_redirects=False,
    )

    # Submit with empty name and short password
    response = client.post(
        "/edit-profile",
        data={
            "name": "",
            "password": "123",  # too short
            "skills_can_teach": "Python",
            "skills_want_to_learn": "Docker",
            "bio": "Bio",
        },
    )
    assert response.status_code == 200
    assert "Name is required" in response.text
    assert "at least 6 characters" in response.text


if __name__ == "__main__":
    test_edit_profile_requires_login()
    print("PASS: requires login")
    test_edit_profile_get_prefilled()
    print("PASS: get prefilled")
    test_edit_profile_post_updates()
    print("PASS: post updates")
    test_edit_profile_post_with_new_password()
    print("PASS: post with new password")
    test_edit_profile_validation()
    print("PASS: validation")
    print("\nALL EDIT PROFILE TESTS PASSED")