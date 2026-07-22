from app.core.database import SessionLocal
from app.actions.bulk_update import BulkUpdateAction


db = SessionLocal()

BulkUpdateAction().execute(
    db=db,
    entity_ids=[1, 2, 3],
    payload={
        "status": "INACTIVE",
        "age": 99,
    },
    bulk_action=None,
)

print("Done")