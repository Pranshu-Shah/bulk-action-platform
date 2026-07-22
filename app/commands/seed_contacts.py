from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.contact import Contact


TOTAL_CONTACTS = 5000


def seed_contacts(db: Session):
    existing = db.query(Contact).count()

    if existing > 0:
        print(f"Contacts table already has {existing} rows, skipping seed.")
        return

    contacts = []

    for i in range(1, TOTAL_CONTACTS + 1):
        contacts.append(
            Contact(
                name=f"Contact {i}",
                email=f"contact{i}@gmail.com",
                status="ACTIVE",
                age=20 + (i % 40),
            )
        )

    db.bulk_save_objects(contacts)
    db.commit()

    print(f"Inserted {TOTAL_CONTACTS} contacts")


if __name__ == "__main__":
    db = SessionLocal()

    try:
        seed_contacts(db)
    finally:
        db.close()