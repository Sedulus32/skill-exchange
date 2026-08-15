"""Quick test for the Feedback system routes."""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# Test GET /feedback
r1 = client.get("/feedback")
print(f"GET /feedback: {r1.status_code}")
assert r1.status_code == 200, f"Expected 200, got {r1.status_code}"

# Test POST /feedback with valid data
r2 = client.post("/feedback", data={"name": "Test User", "message": "Great app!"})
print(f"POST /feedback (valid): {r2.status_code}")
assert r2.status_code == 200, f"Expected 200, got {r2.status_code}"

# Test POST /feedback with empty message (should show error)
r3 = client.post("/feedback", data={"name": "Test User", "message": ""})
print(f"POST /feedback (empty message): {r3.status_code}")
assert r3.status_code == 200, f"Expected 200, got {r3.status_code}"
assert "Message is required" in r3.text, "Error message not shown"

# Log in as admin to access admin pages
r_login = client.post("/admin/login", data={"password": "admin123"}, follow_redirects=False)
print(f"POST /admin/login: {r_login.status_code}")
assert r_login.status_code == 303, f"Expected 303, got {r_login.status_code}"

# Test GET /admin/feedbacks
r4 = client.get("/admin/feedbacks")
print(f"GET /admin/feedbacks: {r4.status_code}")
assert r4.status_code == 200, f"Expected 200, got {r4.status_code}"

# Verify the feedback was saved
r5 = client.get("/admin/feedbacks")
assert "Great app!" in r5.text, "Submitted feedback not found in admin page"

print("\nAll feedback tests passed!")