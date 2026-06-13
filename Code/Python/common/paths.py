"""
Shared filesystem paths.

One place decides where the database and the live snapshot live, so the
collector (writer), dashboard server (reader), and static exporter (reader)
can never disagree.

Database and persistent field state go on durable storage (the SSD on the
Pi). The live creature_state.json snapshot is rewritten every tick and is
worthless after a restart, so it goes to tmpfs (/dev/shm) when available:
~100 KB/s of writes that would otherwise hit the SSD around the clock.

Overrides:
    CREATURE_DB_PATH          full path to the SQLite database
    CREATURE_STATE_JSON_PATH  full path to the live snapshot JSON
"""

import os

LOCAL_DB_PATH = "data/creature_raw_light.db"
SSD_DB_PATH = "/mnt/creatureSSD/Creature/database/creature_raw_light.db"

if os.environ.get("CREATURE_DB_PATH"):
    DB_PATH = os.environ["CREATURE_DB_PATH"]
elif os.path.exists("/mnt/creatureSSD"):
    DB_PATH = SSD_DB_PATH
else:
    DB_PATH = LOCAL_DB_PATH

DB_DIR = os.path.dirname(DB_PATH)

# Live snapshot: tmpfs when the OS provides it (Pi/Linux), DB dir otherwise (Mac dev).
if os.environ.get("CREATURE_STATE_JSON_PATH"):
    STATE_JSON_PATH = os.environ["CREATURE_STATE_JSON_PATH"]
elif os.path.isdir("/dev/shm"):
    STATE_JSON_PATH = "/dev/shm/creature/creature_state.json"
else:
    STATE_JSON_PATH = os.path.join(DB_DIR, "creature_state.json")

# Persistent field state (structure + slow traits). Durable storage, never tmpfs.
FIELD_STATE_PATH = os.path.join(DB_DIR, "creature_field_state.json")
