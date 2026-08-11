import time

from fastapi.testclient import TestClient

# Import the app AFTER the DB has the password column
from main import app

client = TestClient(app)


def unique_email(name: str) -> str:
    return f"{name}-{int(time.time() * 1000)}@example.com"


def test_register_with_password_and_login():
    email = unique_email("alice")

    # 1. Register via the form (POST /register)
    response = client.post(
        "/register",
        data={
            "name": "Alice",
            "email": email,
            "password": "secret123",
            "skills_can_teach": "Python, SQL",
            "skills_want_to_learn": "FastAPI, Docker",
            "bio": "I love teaching Python!",
        },
    )
    print("Register status:", response.status_code)
    assert response.status_code == 200
    assert "Your profile has been created" in response.text

    # 2. The user should be auto-logged in (session cookie set)
    assert client.cookies.get("session") is not None

    # 3. Home page should now show the user's name (logged in navbar)
    response = client.get("/")
    print("Home status:", response.status_code)
    assert response.status_code == 200
    assert "Alice" in response.text
    assert "Logout" in response.text

    # 4. Log out
    response = client.get("/logout", follow_redirects=False)
    print("Logout status:", response.status_code)
    assert response.status_code == 303

    # 5. Now not logged in - navbar should show Login/Register
    response = client.get("/")
    assert "Login" in response.text
    assert "Register" in response.text
    assert "Logout" not in response.text

    # 6. Login with wrong password -> error
    response = client.post(
        "/login",
        data={"email": email, "password": "wrongpass"},
    )
    print("Wrong password status:", response.status_code)
    assert response.status_code == 200
    assert "Invalid email or password" in response.text

    # 7. Login with correct password -> redirect to profile
    response = client.post(
        "/login",
        data={"email": email, "password": "secret123"},
        follow_redirects=False,
    )
    print("Correct login status:", response.status_code)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/user/")

    # 8. Follow redirect and confirm logged-in navbar
    response = client.get(response.headers["location"])
    assert response.status_code == 200
    assert "Alice" in response.text
    assert "Logout" in response.text

    print("\nLOGIN TESTS PASSED")


def test_matches_uses_logged_in_user():
    # Register a user with a known password
    email = unique_email("bob")
    client.post(
        "/register",
        data={
            "name": "Bob",
            "email": email,
            "password": "secret123",
            "skills_can_teach": "FastAPI",
            "skills_want_to_learn": "Python",
            "bio": "FastAPI enthusiast",
        },
    )

    # The user is auto-logged in. Visit /matches - no user_id needed.
    response = client.get("/matches")
    print("Matches status:", response.status_code)
    assert response.status_code == 200
    # Should show that it's using the logged-in user automatically
    assert "Showing matches for" in response.text
    assert "Bob" in response.text
    # The manual ID form should NOT be shown
    assert 'name="user_id"' not in response.text

    print("\nMATCHES AUTO-LOGIN TEST PASSED")


if __name__ == "__main__":
    test_register_with_password_and_login()
    test_matches_uses_logged_in_user()
    print("\nALL LOGIN TESTS PASSED")