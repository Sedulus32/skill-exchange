"""
Database cleanup script for the Skill Exchange app.
Keeps only the real user accounts (bilal, mark).
Deletes all test/duplicate users and all messages involving them.
"""
import sqlite3

DB_PATH = "skill_exchange.db"
KEEP_USER_IDS = {64, 75}

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT id, name, email FROM users ORDER BY id")
    all_users = cur.fetchall()
    print(f"Total users before cleanup: {len(all_users)}")

    test_user_ids = [u[0] for u in all_users if u[0] not in KEEP_USER_IDS]
    print(f"Test users to delete: {len(test_user_ids)}")

    placeholders = ",".join("?" for _ in test_user_ids)

    cur.execute(
        f"DELETE FROM messages WHERE sender_id IN ({placeholders}) OR receiver_id IN ({placeholders})",
        test_user_ids + test_user_ids
    )
    print(f"Messages deleted: {cur.rowcount}")

    cur.execute(f"DELETE FROM users WHERE id IN ({placeholders})", test_user_ids)
    print(f"Users deleted: {cur.rowcount}")

    conn.commit()

    cur.execute("SELECT id, name, email FROM users ORDER BY id")
    remaining = cur.fetchall()
    print(f"\nRemaining users after cleanup: {len(remaining)}")
    for u in remaining:
        print(f"  ID {u[0]}: {u[1]} ({u[2]})")

    cur.execute("SELECT COUNT(*) FROM messages")
    print(f"Remaining messages: {cur.fetchone()[0]}")

    conn.close()

if __name__ == "__main__":
    main()