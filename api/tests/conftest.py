import os

os.environ["DATABASE_URL"] = "sqlite:///./test_console.db"
os.environ["REDIS_FAKE"] = "1"
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "admin12345")
os.environ["LOGIN_RATE_PER_MINUTE"] = "20"
os.environ["ENV"] = "test"
