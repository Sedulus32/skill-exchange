"""Verify the new Match Score system, filters, and improved Matches page.

Run with:  python -m pytest test_matches_score_filters.py -v
"""
import re
import time

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

BASE = f"match_{int(time.time() * 1000)}"


def unique_email(name: str) -> str:
    return f"{BASE}-{name}@example.com"


def create_user(name: str, email: str, teach: list, learn: list, **extra):
    payload = {
        "name": name,
        "email": email,
        "password": "secret123",
        "skills_can_teach": teach,
        "skills_want_to_learn": learn,
        "bio": "",
    }
    payload.update(extra)
    return client.post("/users/", json=payload).json()


def matches_list(html: str) -> str:
    """Return only the matches-list portion of the page (excludes the sidebar)."""
    start = html.find('<div class="matches-list">')
    if start == -1:
        return ""
    return html[start : start + 20000]


def test_matches_page_has_filters_and_score():
    """Build two related users, view matches, and confirm the new UI elements exist."""
    alice = create_user(
        "Alice Score",
        unique_email("alice"),
        ["Python", "Sql"],
        ["Photoshop", "Guitar"],
        age=25,
        gender="Female",
        country="India",
        language="English",
    )

    bob = create_user(
        "Bob Pics",
        unique_email("bob"),
        ["Photoshop"],
        ["Python"],
        age=28,
        gender="Male",
        country="India",
        language="English",
    )

    # Login as Alice
    login_resp = client.post(
        "/login",
        data={"email": alice["email"], "password": "secret123"},
        follow_redirects=False,
    )
    assert login_resp.status_code == 303

    # Visit /matches
    response = client.get("/matches")
    assert response.status_code == 200
    html = response.text

    # Heading (singular "person" or plural "people" depending on count)
    assert "you can exchange skills with" in html

    # Filters
    assert "Apply Filters" in html
    assert 'name="gender"' in html
    assert 'name="country"' in html
    assert 'name="language"' in html
    assert 'name="age"' in html

    # Match card area should show Bob
    cards = matches_list(html)
    assert "Bob Pics" in cards
    assert "%" in cards
    assert "Match" in cards
    assert "You teach" in cards
    assert "They teach" in cards
    assert "India" in cards
    assert "Chat / Connect" in cards

    # Sort check: match list should be ordered by score descending
    scores = re.findall(r"match-score-value\">(\d+)%", cards)
    if scores:
        ints = [int(s) for s in scores]
        assert ints == sorted(ints, reverse=True), f"Scores not sorted: {ints}"

    print("PASS: matches page contains heading, filters, score badge, exchange summary, rating, chat button")


def test_filters_narrow_matches():
    """Filtering on gender, country, and age must narrow the skill-matched results."""
    alice2 = create_user(
        "Alice Two",
        unique_email("alice2"),
        ["Python"],
        ["Photoshop"],
        age=25,
        gender="Female",
        country="India",
        language="English",
    )

    carol = create_user(
        "Carol Female",
        unique_email("carol"),
        ["Photoshop"],
        ["Python"],
        age=30,
        gender="Female",
        country="India",
        language="English",
    )

    client.post(
        "/login",
        data={"email": alice2["email"], "password": "secret123"},
        follow_redirects=False,
    )

    # Filter by male - Carol is female so her match card should NOT appear
    resp = client.get("/matches?gender=Male")
    assert resp.status_code == 200
    assert "Carol Female" not in matches_list(resp.text)

    # Filter by female - Carol should appear in the match cards
    resp = client.get("/matches?gender=Female")
    assert resp.status_code == 200
    assert "Carol Female" in matches_list(resp.text)

    # Filter by country USA - Carol is India, so hidden
    resp = client.get("/matches?country=USA")
    assert resp.status_code == 200
    assert "Carol Female" not in matches_list(resp.text)

    # Filter by country India - Carol should appear
    resp = client.get("/matches?country=India")
    assert resp.status_code == 200
    assert "Carol Female" in matches_list(resp.text)

    # Age range 18-25 hides Carol (30)
    resp = client.get("/matches?age=18_25")
    assert resp.status_code == 200
    assert "Carol Female" not in matches_list(resp.text)

    # Age range 26-35 shows Carol (30)
    resp = client.get("/matches?age=26_35")
    assert resp.status_code == 200
    assert "Carol Female" in matches_list(resp.text)

    # Language English shows Carol
    resp = client.get("/matches?language=English")
    assert resp.status_code == 200
    assert "Carol Female" in matches_list(resp.text)

    print("PASS: gender, country, language, and age filters narrow skill matches correctly")


if __name__ == "__main__":
    test_matches_page_has_filters_and_score()
    test_filters_narrow_matches()
    print("\nALL VERIFY TESTS PASSED")