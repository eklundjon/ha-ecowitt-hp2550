"""Constants for the Ecowitt HP2550 console integration."""

DOMAIN = "ecowitt_hp2550"

# On v2 the status is authoritative and follows keypad changes; on v1 it only
# reports the last command, so polling mostly recovers state after a restart.
# Each read is a connection to the console's WiFi module, so keep it gentle.
UPDATE_INTERVAL = 30

# Level to restore when the auto-brightness switch is turned off before the
# integration has seen a numeric level (e.g. auto was already on at startup).
# The status shows "A" instead of the saved level while auto is on.
DEFAULT_LEVEL = 50
