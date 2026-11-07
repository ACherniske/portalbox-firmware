# tests/core/test_fsm.py
# Manual FSM state validation test

import uasyncio as asyncio

from core.fsm import (
    DeviceFSM,
    States,
    STATE_NAMES
)

from core.device import cleanup_device


STATE_DURATION = 5


async def hold_state(
    fsm,
    state,
    duration = STATE_DURATION
):
    """
    Transition to a state and hold it.

    Args:
        fsm: FSM instance
        state: FSM state enum
        duration: Seconds to remain in state
    """

    print("\n================================================")
    print(f"[TEST] Transitioning to {STATE_NAMES[state]}")
    print("================================================")

    fsm._set_state(state)

    for second in range(duration):

        print(
            f"[TEST] Active State: "
            f"{STATE_NAMES[fsm.state]} "
            f"({second + 1}/{duration}s)"
        )

        await asyncio.sleep(1)


async def main():
    """Run FSM state tests."""

    print("\n==============================")
    print(" FSM STATE TEST")
    print("==============================")

    fsm = DeviceFSM()

    # Apply initial hardware state
    fsm._apply_state()

    await asyncio.sleep(2)

    # =========================================================
    # IDLE
    # =========================================================

    await hold_state(
        fsm,
        States.IDLE
    )

    # =========================================================
    # AUTH
    # =========================================================

    await hold_state(
        fsm,
        States.AUTH
    )

    # =========================================================
    # NOAUTH
    # =========================================================

    await hold_state(
        fsm,
        States.NOAUTH
    )

    # =========================================================
    # GRACE
    # =========================================================

    await hold_state(
        fsm,
        States.GRACE
    )

    # =========================================================
    # PROXY
    # =========================================================

    await hold_state(
        fsm,
        States.PROXY
    )

    # =========================================================
    # TRAINING
    # =========================================================

    await hold_state(
        fsm,
        States.TRAINING
    )

    # =========================================================
    # TIMEOUT
    # =========================================================

    await hold_state(
        fsm,
        States.TIMEOUT
    )

    # =========================================================
    # Return to IDLE
    # =========================================================

    print("\n================================================")
    print("[TEST] Returning to IDLE")
    print("================================================")

    fsm._set_state(
        States.IDLE
    )

    await asyncio.sleep(3)

    # =========================================================
    # Cleanup
    # =========================================================

    print("\n================================================")
    print("[TEST] Cleaning up")
    print("================================================")

    fsm.stop()

    cleanup_device()

    print("\n==============================")
    print(" FSM TEST COMPLETE")
    print("==============================\n")


# ============================================================================
# Run Test
# ============================================================================

try:

    asyncio.run(main())

except KeyboardInterrupt:

    print("\n[TEST] Interrupted by user")

    cleanup_device()
