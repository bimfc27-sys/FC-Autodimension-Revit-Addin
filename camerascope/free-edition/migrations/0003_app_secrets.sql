-- Fallback storage for the credential encryption key when no CREDENTIAL_KEY secret is
-- configured (iPhone-only setups). Setting the CREDENTIAL_KEY secret is stronger: then a
-- copy of the database alone cannot decrypt camera passwords.
CREATE TABLE app_secrets (
  name TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
