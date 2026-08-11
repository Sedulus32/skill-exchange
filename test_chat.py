import time

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def unique_email(name: str) -> str:
    """Generate a unique email so tests can be re-run without conflicts."""
    return f"{name}-{int(time.time() * 1000)}@example.com"


def create_user_via_api(name: str, email: str, password: str = "secret123"):
    """Create a user via the API and return the user dict (with id)."""
    response = client.post(
        "/users/",
        json={
            "name": name,
            "email": email,
            "password": password,
            "skills_can_teach": ["Python", "SQL"],
            "skills_want_to_learn": ["FastAPI", "Docker"],
            "bio": f"Bio for {name}",
        },
    )
    assert response.status_code == 200
    return response.json()


def login(email: str, password: str = "secret123"):
    """Log in via the web form and return the response."""
    response = client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    print(f"  Login status for {email}: {response.status_code}")
    print(f"  Login redirect: {response.headers.get('location', 'N/A')}")
    assert response.status_code == 303, f"Login failed for {email}: status {response.status_code}"
    return response


def test_chat_requires_login():
    """The chat page should redirect to /login when not logged in."""
    # Clear any session
    client.get("/logout")

    response = client.get("/chat/1", follow_redirects=False)
    print("Chat without login status:", response.status_code)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    print("CHAT LOGIN TEST PASSED")


def test_chat_send_and_display():
    """Two users can exchange messages and see them in order."""
    # Create two users via API
    alice = create_user_via_api("Alice", unique_email("alice"))
    bob = create_user_via_api("Bob", unique_email("bob"))

    # Log in as Alice
    client.get("/logout")
    login(alice["email"])

    # Alice sends a message to Bob
    response = client.post(
        f"/chat/{bob['id']}",
        data={"content": "Hello Bob! Want to trade Python for FastAPI?"},
        follow_redirects=False,
    )
    print("Send message status:", response.status_code)
    assert response.status_code == 303
    assert response.headers["location"] == f"/chat/{bob['id']}"

    # Alice views the chat page with Bob
    response = client.get(f"/chat/{bob['id']}")
    print("Chat page status:", response.status_code)
    assert response.status_code == 200
    assert "Hello Bob! Want to trade Python for FastAPI?" in response.text

    # Bob logs in and replies
    client.get("/logout")
    login(bob["email"])

    response = client.post(
        f"/chat/{alice['id']}",
        data={"content": "Sure! Let's do it."},
        follow_redirects=False,
    )
    print(f"Bob reply status: {response.status_code}")
    print(f"Bob reply redirect: {response.headers.get('location', 'N/A')}")
    assert response.status_code == 303

    # Bob views the chat with Alice - both messages should appear in order
    response = client.get(f"/chat/{alice['id']}")
    assert response.status_code == 200
    text = response.text
    alice_pos = text.find("Hello Bob! Want to trade Python for FastAPI?")
    # Note: Jinja2 auto-escapes the apostrophe in Bob's message
    bob_pos = text.find("Sure! Let&#39;s do it.")
    assert alice_pos != -1, "Alice's message not found"
    assert bob_pos != -1, f"Bob's message not found (escaped: {bob_pos})"
    assert alice_pos < bob_pos, "Messages not in chronological order (oldest first)"

    print("CHAT SEND & DISPLAY TEST PASSED")


def test_chat_buttons_present():
    """Chat buttons should appear on matched cards and user profile pages."""
    # Create a user and log in
    user = create_user_via_api("ChatUser", unique_email("chatuser"))
    client.get("/logout")
    login(user["email"])

    # Visit the user's own profile - should NOT show a Chat button (can't chat with self)
    response = client.get(f"/user/{user['id']}")
    assert response.status_code == 200
    assert f"/chat/{user['id']}" not in response.text

    # Create another user who matches ChatUser
    # ChatUser teaches Python/SQL, wants FastAPI/Docker
    # OtherUser should teach FastAPI and want Python -> mutual match
    other_resp = client.post(
        "/users/",
        json={
            "name": "OtherUser",
            "email": unique_email("other"),
            "password": "secret123",
            "skills_can_teach": ["FastAPI"],
            "skills_want_to_learn": ["Python"],
            "bio": "Other user",
        },
    )
    assert other_resp.status_code == 200
    other = other_resp.json()

    # Visit another user's profile - should show a Chat button
    response = client.get(f"/user/{other['id']}")
    assert response.status_code == 200
    assert f"/chat/{other['id']}" in response.text

    # Visit the matches page - should show Chat buttons on matched cards
    response = client.get("/matches")
    assert response.status_code == 200
    assert f"/chat/{other['id']}" in response.text

    print("CHAT BUTTONS TEST PASSED")


if __name__ == "__main__":
    test_chat_requires_login()
    test_chat_send_and_display()
    test_chat_buttons_present()
    print("\nALL CHAT TESTS PASSED")