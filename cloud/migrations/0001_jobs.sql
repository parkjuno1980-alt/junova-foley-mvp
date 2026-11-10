PRAGMA foreign_keys=ON;
CREATE TABLE invites (
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
 expires_at INTEGER NOT NULL, max_jobs INTEGER NOT NULL DEFAULT 10,
 revoked INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, invite_id TEXT NOT NULL REFERENCES invites(id), title TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'uploading', stage TEXT, percent REAL NOT NULL DEFAULT 0,
 message TEXT NOT NULL DEFAULT '', error TEXT, config TEXT NOT NULL, engine_mode TEXT NOT NULL DEFAULT 'unknown',
 input_bytes INTEGER NOT NULL, reserved_bytes INTEGER NOT NULL,
 lease TEXT, lease_until INTEGER, summary TEXT NOT NULL DEFAULT '{}',
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX jobs_owner ON jobs(invite_id,created_at);
CREATE INDEX jobs_queue ON jobs(status,created_at);
CREATE TABLE files (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL,
 name TEXT NOT NULL, object_key TEXT NOT NULL UNIQUE, upload_id TEXT,
 size INTEGER NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
 UNIQUE(job_id,kind,name)
);
CREATE TABLE parts (
 file_id TEXT NOT NULL REFERENCES files(id), number INTEGER NOT NULL,
 etag TEXT NOT NULL, size INTEGER NOT NULL, PRIMARY KEY(file_id,number)
);
CREATE TABLE worker_state (id INTEGER PRIMARY KEY CHECK(id=1), last_seen INTEGER NOT NULL);
-- Fixed free-tier envelope. Raising it requires an explicit billing review.
CREATE TRIGGER jobs_capacity BEFORE INSERT ON jobs BEGIN
 SELECT CASE WHEN NEW.reserved_bytes + COALESCE((SELECT SUM(reserved_bytes) FROM jobs WHERE status!='purged'),0)>9000000000
 THEN RAISE(ABORT,'capacity') END;
 SELECT CASE WHEN (SELECT COUNT(*) FROM jobs WHERE invite_id=NEW.invite_id)>=(SELECT max_jobs FROM invites WHERE id=NEW.invite_id)
 THEN RAISE(ABORT,'invite_limit') END;
END;
