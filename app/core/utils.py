def chunk_list(items, chunk_size):
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def register_or_flag_duplicate(entity, seen_keys: set, get_dedup_key) -> bool:
    """
    Records `entity`'s dedup key into `seen_keys` (mutated in place) and
    returns True if it's a repeat of a key already seen - False if it's
    new, or if this entity type has no dedup key (get_dedup_key returns
    None). Extracted as a standalone function so the de-duplication
    algorithm can be unit-tested directly, without needing two real rows
    that share a key - which, for Contact specifically, the database's
    own unique constraint on email makes impossible to construct.
    """
    key = get_dedup_key(entity)

    if key is None:
        return False

    if key in seen_keys:
        return True

    seen_keys.add(key)
    return False