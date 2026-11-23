// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Medical Camp", {
	setup: function (frm) {
		frm.set_query("appointment_type", function () {
			return { filters: { allow_booking_for: "Medical Camp" } };
		});

		frm.set_query("practitioner", "specialities", function (doc, cdt, cdn) {
			let row = locals[cdt][cdn];
			return row.medical_department
				? { filters: { department: row.medical_department } }
				: {};
		});
	},

	validate: function (frm) {
		// the grid starts with a blank row; drop it so it does not trip the mandatory check
		(frm.doc.specialities || [])
			.filter((row) => !row.medical_department && !row.practitioner)
			.forEach((row) => frappe.model.clear_doc(row.doctype, row.name));
		frm.refresh_field("specialities");
	},

	refresh: function (frm) {
		if (frm.is_new() || frm.doc.status === "Cancelled") return;

		frm.add_custom_button(__("Register Patient"), function () {
			frappe.new_doc("Patient", { patient_source: "Medical Camp", medical_camp: frm.doc.name });
		});

		frm.add_custom_button(
			__("Book Appointment"),
			function () {
				frappe.new_doc("Patient Appointment", {
					appointment_type: frm.doc.appointment_type,
					appointment_for: "Medical Camp",
					medical_camp: frm.doc.name,
					company: frm.doc.company,
				});
			}
		);
	},
});

frappe.ui.form.on("Medical Camp Speciality", {
	practitioner: function (frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (!row.practitioner || row.medical_department) return;
		frappe.db.get_value("Healthcare Practitioner", row.practitioner, "department").then((r) => {
			if (r.message && r.message.department) {
				frappe.model.set_value(cdt, cdn, "medical_department", r.message.department);
			}
		});
	},
});
