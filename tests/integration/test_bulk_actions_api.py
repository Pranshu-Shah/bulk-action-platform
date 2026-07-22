from app.enums.bulk_status import BulkActionStatus

MISSING_CONTACT_ID = 999_999_999


class TestCreateAndProcessBulkAction:
    """
    Celery's `.delay()` runs eagerly (see conftest.celery_eager) - by the
    time `client.post(...)` returns, dispatch_bulk_action and every
    process_bulk_action_batch it enqueued have already run inline,
    against the real DB, inside this same test's transaction.
    """

    def test_bulk_update_runs_end_to_end(self, client, db_session, contacts):
        response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_update",
                "entity_type": "contact",
                "entity_ids": [c.id for c in contacts],
                "payload": {"status": "INACTIVE", "age": 55},
            },
        )
        assert response.status_code == 200
        bulk_action_id = response.json()["id"]

        status_response = client.get(f"/bulk-actions/{bulk_action_id}")
        assert status_response.status_code == 200
        assert status_response.json()["status"] == BulkActionStatus.COMPLETED.value

        stats_response = client.get(f"/bulk-actions/{bulk_action_id}/stats")
        stats = stats_response.json()
        assert stats["total"] == len(contacts)
        assert stats["succeeded"] == len(contacts)
        assert stats["failed"] == 0
        assert stats["skipped"] == 0

        progress_response = client.get(f"/bulk-actions/{bulk_action_id}/progress")
        assert progress_response.json()["percent_complete"] == 100.0

        for contact in contacts:
            db_session.refresh(contact)
            assert contact.status == "INACTIVE"
            assert contact.age == 55

        logs_response = client.get(f"/bulk-actions/{bulk_action_id}/logs")
        logs = logs_response.json()["items"]
        assert len(logs) == len(contacts)
        assert all(log["status"] == "SUCCESS" for log in logs)

    def test_missing_contact_id_is_skipped_not_a_failure(self, client, contacts):
        response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_update",
                "entity_type": "contact",
                "entity_ids": [contacts[0].id, MISSING_CONTACT_ID],
                "payload": {"status": "INACTIVE"},
            },
        )
        bulk_action_id = response.json()["id"]

        stats = client.get(f"/bulk-actions/{bulk_action_id}/stats").json()
        assert stats["total"] == 2
        assert stats["succeeded"] == 1
        assert stats["skipped"] == 1

        status_response = client.get(f"/bulk-actions/{bulk_action_id}")
        assert status_response.json()["status"] == BulkActionStatus.COMPLETED.value

    def test_all_missing_contact_ids_still_completes(self, client):
        """Regression test for the zero-batch-dispatched edge case."""
        response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_update",
                "entity_type": "contact",
                "entity_ids": [MISSING_CONTACT_ID],
                "payload": {"status": "INACTIVE"},
            },
        )
        bulk_action_id = response.json()["id"]

        status_response = client.get(f"/bulk-actions/{bulk_action_id}")
        assert status_response.json()["status"] == BulkActionStatus.COMPLETED.value

        stats = client.get(f"/bulk-actions/{bulk_action_id}/stats").json()
        assert stats["total"] == 1
        assert stats["skipped"] == 1


class TestValidationErrors:

    def test_unsupported_action_type_returns_400(self, client, contacts):
        response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_frobnicate",
                "entity_type": "contact",
                "entity_ids": [contacts[0].id],
                "payload": {},
            },
        )
        assert response.status_code == 400

    def test_empty_payload_for_bulk_update_returns_400(self, client, contacts):
        response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_update",
                "entity_type": "contact",
                "entity_ids": [contacts[0].id],
                "payload": {},
            },
        )
        assert response.status_code == 400

    def test_entity_ids_empty_list_rejected_by_schema(self, client):
        response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_update",
                "entity_type": "contact",
                "entity_ids": [],
                "payload": {"status": "INACTIVE"},
            },
        )
        assert response.status_code == 422


class TestGetBulkAction:

    def test_not_found_returns_404(self, client):
        response = client.get(f"/bulk-actions/{MISSING_CONTACT_ID}")
        assert response.status_code == 404


class TestListBulkActions:

    def test_lists_and_paginates(self, client, contacts):
        ids = []
        for _ in range(3):
            response = client.post(
                "/bulk-actions",
                json={
                    "action_type": "bulk_export",
                    "entity_type": "contact",
                    "entity_ids": [contacts[0].id],
                    "payload": {},
                },
            )
            ids.append(response.json()["id"])

        list_response = client.get("/bulk-actions", params={"limit": 2, "offset": 0})
        body = list_response.json()
        assert body["total"] >= 3
        assert len(body["items"]) == 2
        # sort=desc by default - newest (highest id) first.
        assert body["items"][0]["id"] > body["items"][1]["id"]

    def test_filters_by_action_type(self, client, contacts):
        client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_archive",
                "entity_type": "contact",
                "entity_ids": [contacts[0].id],
                "payload": {},
            },
        )

        response = client.get("/bulk-actions", params={"action_type": "bulk_archive"})
        body = response.json()
        assert all(item["action_type"] == "bulk_archive" for item in body["items"])


class TestCancelBulkAction:

    def test_cancel_already_completed_returns_409(self, client, contacts):
        create_response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_archive",
                "entity_type": "contact",
                "entity_ids": [contacts[0].id],
                "payload": {},
            },
        )
        bulk_action_id = create_response.json()["id"]

        # Eager mode means it's already COMPLETED by the time create() returns.
        cancel_response = client.post(f"/bulk-actions/{bulk_action_id}/cancel")
        assert cancel_response.status_code == 409

    def test_cancel_not_found_returns_404(self, client):
        response = client.post(f"/bulk-actions/{MISSING_CONTACT_ID}/cancel")
        assert response.status_code == 404


class TestLogsPagination:

    def test_cursor_pagination_walks_all_logs(self, client, contacts):
        create_response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_export",
                "entity_type": "contact",
                "entity_ids": [c.id for c in contacts],
                "payload": {},
            },
        )
        bulk_action_id = create_response.json()["id"]

        seen_ids = set()
        after_id = 0

        for _ in range(len(contacts)):
            page = client.get(
                f"/bulk-actions/{bulk_action_id}/logs",
                params={"after_id": after_id, "limit": 2},
            ).json()

            if not page["items"]:
                break

            for item in page["items"]:
                seen_ids.add(item["entity_id"])

            if page["next_after_id"] is None:
                break

            after_id = page["next_after_id"]

        assert seen_ids == {c.id for c in contacts}

    def test_filters_by_status(self, client, contacts):
        create_response = client.post(
            "/bulk-actions",
            json={
                "action_type": "bulk_update",
                "entity_type": "contact",
                "entity_ids": [contacts[0].id, MISSING_CONTACT_ID],
                "payload": {"status": "INACTIVE"},
            },
        )
        bulk_action_id = create_response.json()["id"]

        skipped = client.get(
            f"/bulk-actions/{bulk_action_id}/logs",
            params={"status": "SKIPPED"},
        ).json()["items"]
        assert len(skipped) == 1
        assert skipped[0]["status"] == "SKIPPED"

        succeeded = client.get(
            f"/bulk-actions/{bulk_action_id}/logs",
            params={"status": "SUCCESS"},
        ).json()["items"]
        assert len(succeeded) == 1
        assert succeeded[0]["entity_id"] == contacts[0].id
