# SUPPORTED_ACTIONS and SUPPORTED_ENTITY_TYPES are no longer separately
# maintained lists - they'd drift from ActionRegistry.supported_actions()
# / EntityRegistry.supported_types(), which are the actual source of
# truth for what's registered. Callers should use those instead.

# Actions whose payload is a set of entity fields to overwrite - the only
# ones validated against the target entity's get_updatable_fields().
FIELD_UPDATE_ACTIONS = {
    "bulk_update",
}

# Actions that need no payload at all.
NO_PAYLOAD_ACTIONS = {
    "bulk_delete",
    "bulk_archive",
    "bulk_export",
}

ASSIGN_OWNER_FIELD = "owner_id"

ARCHIVED_STATUS = "ARCHIVED"

# BulkDeleteAction is a soft delete: bulk_action_items.contact_id has a
# permanent FK to contacts (it's an audit table, rows are never removed),
# so a real DELETE on a contact that's ever been touched by any bulk
# action would always violate that FK. Setting status instead - same
# pattern as ARCHIVED_STATUS - keeps the row and the audit trail intact.
DELETED_STATUS = "DELETED"
