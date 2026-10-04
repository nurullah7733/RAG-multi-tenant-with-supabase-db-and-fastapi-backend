"""Tests are isolated from developer credentials and shell settings."""

import os

os.environ["DEBUG"] = "false"
os.environ["SUPABASE_URL"] = "https://supabase.example.test"
os.environ["SUPABASE_PUBLISHABLE_KEY"] = "sb_publishable_test_only"
