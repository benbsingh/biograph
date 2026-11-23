# Copyright (c) 2026, healthcare and Contributors
# License: GNU General Public License v3. See license.txt

from healthcare.kiosk_setup import ensure_kiosk_role_and_permissions


def execute():
	"""Create the Kiosk role with create-only access on Patient and Patient Appointment."""
	ensure_kiosk_role_and_permissions()
