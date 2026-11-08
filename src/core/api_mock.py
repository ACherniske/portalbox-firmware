# core/api_mock.py
# Mock API for development: core.api uses it when the box's api config has "url": "".

# ============================================================================
# TEST CARD DATABASE
# ============================================================================
# Add your RFID UIDs here.
#
# Example format:
# '04A1B2C3D4': {
#     'name': 'Aiden',
#     'auth': True,
#     'trainer': False,
#     'proxy': False
# }
#
# UID strings should match exactly what your RFID reader returns.
# ============================================================================

USERS = {

    # =========================================================
    # Normal authorized user
    # =========================================================

    '23BE9603': {
        'name': 'Authorized User',
        'auth': True,
        'trainer': False,
        'proxy': False
    },

    # =========================================================
    # Trainer card
    # =========================================================

    '73889103': {
        'name': 'Trainer User',
        'auth': True,
        'trainer': True,
        'proxy': False
    },

    # =========================================================
    # Proxy card
    # =========================================================

    'C38EC002': {
        'name': 'Proxy User',
        'auth': True,
        'trainer': False,
        'proxy': True
    },

    # =========================================================
    # Unauthorized card
    # =========================================================

    '83EE8F03': {
        'name': 'Unauthorized User',
        'auth': False,
        'trainer': False,
        'proxy': False
    }
}

# ============================================================================
# USER LOOKUP HELPERS
# ============================================================================

def get_user(uid):
    """
    Get user dictionary from UID.

    Args:
        uid: RFID UID string

    Returns:
        dict or None
    """

    return USERS.get(uid)

# ============================================================================
# AUTH HELPERS
# ============================================================================

def is_auth(uid):
    """
    Check if UID is authorized.

    Args:
        uid: RFID UID string

    Returns:
        bool
    """

    user = get_user(uid)

    if not user:
        return False

    # Only a real True grants it: a reply's "false" or 1 must not.
    return user.get('auth') is True


def is_trainer(uid):
    """
    Check if UID is a trainer card.

    Args:
        uid: RFID UID string

    Returns:
        bool
    """
    user = get_user(uid)

    if not user:
        return False

    # Only a real True grants it: a reply's "false" or 1 must not.
    return user.get('trainer') is True

def is_proxy(uid):
    """
    Check if UID is a proxy card.

    Args:
        uid: RFID UID string

    Returns:
        bool
    """
    user = get_user(uid)

    if not user:
        return False

    # Only a real True grants it: a reply's "false" or 1 must not.
    return user.get('proxy') is True


# ============================================================================
# SERVER CALLS (same answers as core.api)
# ============================================================================

def activate(uid):
    """(True, None) for an authorized card, else (False, None)."""
    return (is_auth(uid), None)


def change_card(activating_uid, new_uid):
    """As the server answers: 'user' for the same card, 'proxy' for a proxy
    card, 'training' when a trainer's session gets a known card, else None."""
    if new_uid == activating_uid:
        return 'user'
    if is_proxy(new_uid):
        return 'proxy'
    if is_trainer(activating_uid) and get_user(new_uid):
        return 'training'
    return None


def deactivate(uid):
    return True


def startup():
    return 'ok'
