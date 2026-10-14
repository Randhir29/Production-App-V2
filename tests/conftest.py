"""Test environment: settings required at import time of app.main (no real services used)."""

import os

os.environ.setdefault("CENTRAL_DATABASE_URL", "mysql+aiomysql://test:test@localhost:3306/prodash")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-" + "t" * 32)
