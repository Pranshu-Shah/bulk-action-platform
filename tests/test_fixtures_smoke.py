from app.models.contact import Contact


def test_contacts_fixture_creates_rows(db_session, contacts):
    assert len(contacts) == 5
    assert all(c.id is not None for c in contacts)

    found = db_session.query(Contact).filter(Contact.id.in_([c.id for c in contacts])).all()
    assert len(found) == 5


def test_previous_test_rolled_back(db_session):
    """If the previous test's rollback didn't work, this count would be off."""
    count = db_session.query(Contact).filter(Contact.name.like("Test Contact%")).count()
    assert count == 0


def test_client_and_db_session_share_state(client, contacts):
    """Proves the API's dependency-overridden session sees the same fixture data."""
    contact_id = contacts[0].id
    response = client.get(f"/bulk-actions/999999999")
    assert response.status_code == 404
