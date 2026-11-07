"""Constants for the Ecowitt HP2550 console integration."""

DOMAIN = "ecowitt_hp2550"

# The status slot only changes when the console acts on a command, and the
# console's own schedule / auto-brightness change the backlight without touching
# it, so polling is mainly to recover state after a restart. Keep it gentle.
UPDATE_INTERVAL = 30
