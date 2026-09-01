# core/feedback.py

PROFILES = {
    # =========================================================
    # FSM states
    # =========================================================
    'idle': {
        'led': {'mode': 'pulse', 'color': 'BLUE', 'min_brightness': 0.05, 'max_brightness': 0.30, 'step': 0.02},
        'buzzer': None,
    },
    'auth': {
        'led': {'mode': 'pulse', 'color': 'GREEN', 'min_brightness': 0.05, 'max_brightness': 0.40, 'step': 0.02},
        'buzzer': {'pattern': 'card_read'},
    },
    'noauth': {
        'led': {'mode': 'pulse', 'color': 'RED', 'min_brightness': 0.05, 'max_brightness': 0.40, 'step': 0.02},
        'buzzer': {'pattern': 'timeout'},
    },
    'grace': {
        'led': {'mode': 'blink', 'color': 'YELLOW', 'on_ms': 500, 'off_ms': 500},
        'buzzer': {'pattern': 'card_repeat', 'interval_ms': 1000},
    },
    'proxy': {
        'led': {'mode': 'pulse', 'color': 'ORANGE', 'min_brightness': 0.05, 'max_brightness': 0.40, 'step': 0.02},
        'buzzer': {'pattern': 'card_read'},
    },
    'training': {
        'led': {'mode': 'pulse', 'color': 'PURPLE', 'min_brightness': 0.05, 'max_brightness': 0.40, 'step': 0.02},
        'buzzer': {'pattern': 'card_read'},
    },
    'timeout': {
        'led': {'mode': 'blink', 'color': 'RED', 'on_ms': 250, 'off_ms': 250},
        'buzzer': {'pattern': 'timeout_repeat', 'interval_ms': 1000},
    },

    # =========================================================
    # Boot states — called from core/system.py SYNCHRONOUSLY, before
    # any asyncio event loop is running.
    # =========================================================
    'booting': {
        'led': {'mode': 'fill', 'color': (10, 10, 10)},
        'buzzer': None,
    },
    'wifi_connecting': {
        'led': {'mode': 'fill', 'color': (0, 25, 25)},
        'buzzer': None,
    },
    'ota_checking': {
        'led': {'mode': 'fill', 'color': (0, 60, 60)},
        'buzzer': None,
    },
    'offline': {
        # Retained for any future ASYNC use (e.g. the FSM signaling a
        # lost connection mid-operation, where an event loop really
        # is running). The boot-time "no connectivity" path in
        # core/system.py does NOT use this profile, it uses _offline_lockout() instead.
        'led': {'mode': 'blink', 'color': 'RED', 'on_ms': 400, 'off_ms': 1200},
        'buzzer': None,
    },
}


def apply_feedback(device, profile_name):
    """
    Apply a named feedback profile's LED/buzzer behavior to the device.
    """
    profile = PROFILES.get(profile_name)

    if profile is None:
        print(f"[Feedback] WARNING: Unknown profile '{profile_name}'")
        device.led_stop()
        device.buzzer_stop()
        return

    _apply_hardware_feedback(device, prefix='led', config=profile.get('led'))
    _apply_hardware_feedback(device, prefix='buzzer', config=profile.get('buzzer'))


def _apply_hardware_feedback(device, prefix, config):
    """
    Generic dispatcher for LED or Buzzer profiles.
    Maps dict keys ('mode' or 'pattern') to method names on the device instance.
    """
    stop_method = getattr(device, f"{prefix}_stop", None)

    if not config:
        if stop_method:
            stop_method()
        return

    # Work on a copy to avoid mutating the immutable PROFILES dict
    kwargs = config.copy()
    action = kwargs.pop('mode' if prefix == 'led' else 'pattern', None)

    # Handle special alias (e.g., mode='off' -> device.led_clear())
    if action == 'off':
        if prefix == 'led':
            device.led_clear()
        elif stop_method:
            stop_method()
        return

    method_name = f"{prefix}_{action}"
    method = getattr(device, method_name, None)

    if callable(method):
        try:
            method(**kwargs)
        except TypeError as e:
            print(f"[Feedback] ERROR: Invalid parameters for '{method_name}': {e}")
            if stop_method:
                stop_method()
    else:
        print(f"[Feedback] WARNING: Unknown {prefix} action '{action}'")
        if stop_method:
            stop_method()