"""Host page of the patient registration kiosk, served at /kiosk.

Same origin and login as the Desk. The shared device signs in as a user holding only the Kiosk role; a System Manager
may open the page to preview it, but every API call still needs the Kiosk role. The HTML carries only what the page needs
to start: the CSRF token, which features are on, the idle timeout and the camp's display name. No patient data, no ABDM
credential. The UI is static JS and CSS under public/kiosk, with no build step.
"""

import json
import os
from urllib.parse import quote

import frappe
import frappe.sessions
from frappe import _

from healthcare.api import kiosk
from healthcare.api import kiosk_common as common
from healthcare.kiosk_setup import KIOSK_ROLE

no_cache = 1
no_sitemap = 1

_PREVIEW_ROLES = ("System Manager",)


def get_context(context):
	if frappe.session.user == "Guest":
		# Send the guest to the login form and back here afterwards (temporary: a cached 301 would stick).
		frappe.local.flags.redirect_location = f"/login?redirect-to={quote('/kiosk', safe='')}"
		raise frappe.Redirect(302)

	roles = set(frappe.get_roles(frappe.session.user))
	if KIOSK_ROLE not in roles and not roles.intersection(_PREVIEW_ROLES):
		frappe.throw(_("You do not have permission to use the kiosk."), frappe.PermissionError)

	context.no_cache = 1
	context.title = _("Patient Registration")
	context.js_url = _asset_url("kiosk.js")
	context.css_url = _asset_url("kiosk.css")
	context.boot_json = _safe_json(_boot(preview=KIOSK_ROLE not in roles))
	return context


def _boot(preview):
	settings = common.get_settings()
	return {
		"csrf_token": frappe.sessions.get_csrf_token(),
		"enabled": bool(settings.enabled),
		"preview": preview,
		**kiosk.config_for(settings),
	}


def _asset_url(filename):
	"""/assets URL with ?v=<mtime> so an edited file is not served stale from the browser cache."""
	path = frappe.get_app_path("healthcare", "public", "kiosk", filename)
	version = int(os.path.getmtime(path)) if os.path.exists(path) else 0
	return f"/assets/healthcare/kiosk/{filename}?v={version}"


def _safe_json(data):
	"""JSON that is safe inside a <script type="application/json"> element: no raw <, > or & can close the tag."""
	return json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
