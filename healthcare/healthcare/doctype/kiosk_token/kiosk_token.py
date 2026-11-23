# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import nowdate


class KioskToken(Document):
	"""A per-day queue token issued at the registration kiosk.

	The name is KT-YYYYMMDD-####, a series that restarts every day, so the name itself is the atomic
	counter. The slip prints this document, which carries no DOB, mobile or ABHA.
	"""

	def autoname(self):
		self.token_date = self.token_date or nowdate()
		day = str(self.token_date).replace("-", "")
		self.name = make_autoname(f"KT-{day}-.####", doc=self)
		self.token_number = int(self.name.rsplit("-", 1)[1])
