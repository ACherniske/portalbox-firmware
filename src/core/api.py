# core/api.py
# Mock API layer for development/testing

import time

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

    'PUT_UID_HERE_1': {
        'name': 'Authorized User',
        'auth': True,
        'trainer': False,
        'proxy': False
    },

    # =========================================================
    # Trainer card
    # =========================================================

    'PUT_UID_HERE_2': {
        'name': 'Trainer User',
        'auth': True,
        'trainer': True,
        'proxy': False
    },

    # =========================================================
    # Proxy card
    # =========================================================

    'PUT_UID_HERE_3': {
        'name': 'Proxy User',
        'auth': True,
        'trainer': False,
        'proxy': True
    },

    # =========================================================
    # Unauthorized card
    # =========================================================

    'PUT_UID_HERE_4': {
        'name': 'Unauthorized User',
        'auth': False,
        'trainer': False,
        'proxy': False
    }
}

# ============================================================================
# ACTIVE SESSION STATE
# ============================================================================

_active_session = None

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

def get_name(uid):
    """
    Get user name from UID.

    Args:
        uid: RFID UID string

    Returns:
        str or None
    """

    user = get_user(uid)

    if user:
        return user.get('name', 'Unknown')

    return None

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

    return user.get('auth', False)


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

    return user.get('trainer', False)

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

    return user.get('proxy', False)


# ============================================================================
# LOGGING HELPERS
# ============================================================================

def _timestamp():
    """Get current timestamp string.

    Returns:
        str: Current timestamp rendered as a string.
    """

    return str(time.time())


def log_session_start(uid):
    """Log successful session start.

    Args:
        uid: User UID
    """

    global _active_session

    _active_session = {
        'uid': uid,
        'start': time.time()
    }

    print(
        '[API] SESSION START | '
        f'user={get_name(uid)} | '
        f'uid={uid} | '
        f'timestamp={_timestamp()}'
    )


def log_session_end(uid):
    """Log session end.

    Args:
        uid: User UID
    """

    global _active_session

    duration = None

    if _active_session:
        duration = int(time.time() - _active_session['start'])

    print(
        '[API] SESSION END | '
        f'user={get_name(uid)} | '
        f'uid={uid} | '
        f'duration={duration}s | '
        f'timestamp={_timestamp()}'
    )

    _active_session = None


def log_session_fail(uid):
    """Log failed authentication attempt.

    Args:
        uid: User UID
    """

    print(
        '[API] SESSION FAIL | '
        f'user={get_name(uid)} | '
        f'uid={uid} | '
        f'timestamp={_timestamp()}'
    )


def log_training_start(trainer_uid, trainee_uid):
    """Log training session start.

    Args:
        trainer_uid: Trainer card UID
        trainee_uid: Trainee UID
    """

    print(
        '[API] TRAINING START | '
        f'trainer={get_name(trainer_uid)} | '
        f'trainer_uid={trainer_uid} | '
        f'trainee={get_name(trainee_uid)} | '
        f'trainee_uid={trainee_uid} | '
        f'timestamp={_timestamp()}'
    )


def log_proxy_start(proxy_uid, original_uid):
    """Log proxy authorization event.

    Args:
        proxy_uid: Proxy card UID
        original_uid: Original user UID
    """

    print(
        '[API] PROXY START | '
        f'proxy={get_name(proxy_uid)} | '
        f'proxy_uid={proxy_uid} | '
        f'original_uid={original_uid} | '
        f'timestamp={_timestamp()}'
    )