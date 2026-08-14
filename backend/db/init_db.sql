-- This file is included in docker-compose.yml for PostgreSQL initialization.
-- It can be extended with additional seed data or constraints.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Tables are created via SQLAlchemy, but this ensures the extension exists.