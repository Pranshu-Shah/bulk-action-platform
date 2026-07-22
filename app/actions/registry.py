from app.actions.bulk_archive import BulkArchiveAction
from app.actions.bulk_assign_owner import BulkAssignOwnerAction
from app.actions.bulk_delete import BulkDeleteAction
from app.actions.bulk_export import BulkExportAction
from app.actions.bulk_update import BulkUpdateAction


class ActionRegistry:

    actions = {
        "bulk_update": BulkUpdateAction(),
        "bulk_delete": BulkDeleteAction(),
        "bulk_archive": BulkArchiveAction(),
        "bulk_assign_owner": BulkAssignOwnerAction(),
        "bulk_export": BulkExportAction(),
    }

    @classmethod
    def get_action(cls, action_name: str):

        action = cls.actions.get(action_name)

        if not action:
            raise ValueError(
                f"Unsupported action: {action_name}"
            )

        return action

    @classmethod
    def supported_actions(cls) -> set[str]:
        return set(cls.actions.keys())
