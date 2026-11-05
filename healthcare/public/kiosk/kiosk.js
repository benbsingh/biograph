/*
 * Patient registration kiosk UI (served at /kiosk). Plain JS, no build step.
 *
 * Rules this file keeps:
 *  - It only calls /api/method/healthcare.api.kiosk.* and healthcare.api.kiosk_abha.*.
 *  - Patient details live in the `S` object only. Nothing goes to web storage, cookies or the URL, and
 *    every reset (done, idle timeout) drops `S` and redraws from nothing.
 *  - Server text is only ever put into the page with textContent, never as HTML.
 */
(function () {
	"use strict";

	var BOOT = JSON.parse(document.getElementById("kiosk-boot").textContent);
	var ROOT = document.getElementById("kiosk-root");
	var KIOSK = "/api/method/healthcare.api.kiosk.";
	var ABHA = "/api/method/healthcare.api.kiosk_abha.";
	var GENDERS = ["Male", "Female", "Other"];

	var S = fresh(); // everything entered or received for the current patient
	var busy = false;
	var idleTimer = null;

	function fresh() {
		return { screen: "welcome", patient: null, mobile: "", txn: null, flow: null, error: "", notice: "" };
	}

	// ----------------------------------------------------------------------------------------------
	// Tiny DOM helper
	// ----------------------------------------------------------------------------------------------

	function h(tag, attrs, children) {
		var el = document.createElement(tag);
		Object.keys(attrs || {}).forEach(function (key) {
			var value = attrs[key];
			if (value === null || value === undefined || value === false) return;
			if (key === "class") el.className = value;
			else if (key.indexOf("on") === 0) el.addEventListener(key.slice(2), value);
			else el.setAttribute(key, value === true ? "" : value);
		});
		[].concat(children === undefined ? [] : children).forEach(function (child) {
			if (child === null || child === undefined || child === false) return;
			el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
		});
		return el;
	}

	function button(label, onclick, kind) {
		return h("button", { type: "button", class: "btn " + (kind || "primary"), onclick: onclick }, label);
	}

	function field(id, label, attrs) {
		return h("div", { class: "field" }, [
			h("label", { for: id }, label),
			h("input", Object.assign({ id: id, name: id, autocomplete: "off" }, attrs)),
		]);
	}

	function value(id) {
		var el = document.getElementById(id);
		return el ? el.value.trim() : "";
	}

	// ----------------------------------------------------------------------------------------------
	// Server calls
	// ----------------------------------------------------------------------------------------------

	function call(base, method, args) {
		return fetch(base + method, {
			method: "POST",
			credentials: "same-origin",
			headers: {
				"Content-Type": "application/json",
				Accept: "application/json",
				"X-Frappe-CSRF-Token": BOOT.csrf_token,
			},
			body: JSON.stringify(args || {}),
		}).then(function (response) {
			return response.text().then(function (text) {
				var body = {};
				try {
					body = JSON.parse(text);
				} catch (e) {
					/* not JSON: fall through to the generic error */
				}
				if (response.ok) return body.message;
				var error = new Error(serverMessage(body, response.status));
				error.status = response.status;
				throw error;
			});
		});
	}

	function serverMessage(body, status) {
		if (status === 429) return "Too many requests. Please wait a minute and try again.";
		if (status === 401 || status === 403) return "This kiosk is not signed in. Please ask for help at the desk.";
		try {
			var messages = JSON.parse(body._server_messages || "[]");
			if (messages.length) {
				var text = JSON.parse(messages[0]).message || "";
				var doc = new DOMParser().parseFromString(text, "text/html");
				if (doc.body.textContent.trim()) return doc.body.textContent.trim();
			}
		} catch (e) {
			/* use the generic text */
		}
		return "Something went wrong. Please ask for help at the desk.";
	}

	// Run a server step: show a busy state, put any error on the current screen, never leave it stuck
	function run(promise, onDone) {
		if (busy) return;
		busy = true;
		S.error = "";
		render();
		promise
			.then(function (result) {
				busy = false;
				onDone(result);
			})
			.catch(function (error) {
				busy = false;
				S.error = error.message;
				render();
			});
	}

	// ----------------------------------------------------------------------------------------------
	// Idle reset: a patient walks away, the next one must never see their details
	// ----------------------------------------------------------------------------------------------

	function touch() {
		clearTimeout(idleTimer);
		if (S.screen === "welcome") return;
		idleTimer = setTimeout(reset, BOOT.idle_timeout * 1000);
	}

	function reset() {
		clearTimeout(idleTimer);
		var hadPatient = !!S.patient;
		S = fresh();
		busy = false;
		render();
		if (hadPatient && !BOOT.preview) call(KIOSK, "end_session").catch(function () {});
	}

	["pointerdown", "keydown", "input", "touchstart"].forEach(function (name) {
		document.addEventListener(name, touch, true);
	});

	// ----------------------------------------------------------------------------------------------
	// Screens
	// ----------------------------------------------------------------------------------------------

	function go(screen) {
		S.screen = screen;
		S.error = "";
		render();
		touch();
	}

	function render() {
		var screens = {
			welcome: welcome,
			form: form,
			result: result,
			booked: booked,
			abhaMethod: abhaMethod,
			abhaAadhaar: abhaAadhaar,
			abhaOtp: abhaOtp,
			abhaAddress: abhaAddress,
			abhaDone: abhaDone,
		};
		var content = unavailable() || screens[S.screen]();
		ROOT.replaceChildren(
			BOOT.preview ? h("p", { class: "banner" }, "Preview: sign in as the Kiosk user to register patients.") : null,
			h("section", { class: "card" }, [
				content,
				S.error ? h("p", { class: "error", role: "alert" }, S.error) : null,
				busy ? h("p", { class: "busy" }, "Please wait...") : null,
			])
		);
		var first = ROOT.querySelector("input, select");
		if (first && !busy) first.focus({ preventScroll: true });
	}

	function unavailable() {
		var message = null;
		if (!BOOT.enabled) message = "Registration is not available right now.";
		else if (!BOOT.camp_active) message = "Registration for this camp is closed.";
		if (!message) return null;
		return h("div", { class: "screen" }, [
			h("h1", {}, message),
			h("p", { class: "lead" }, "Please ask for help at the desk."),
		]);
	}

	function welcome() {
		return h("div", { class: "screen" }, [
			h("h1", {}, "Welcome"),
			BOOT.camp ? h("p", { class: "lead" }, BOOT.camp) : null,
			h("p", { class: "lead" }, "Touch the button to register and get your token."),
			button("Start registration", function () {
				go("form");
			}),
		]);
	}

	function form() {
		var gender = S.draft && S.draft.sex;
		var draft = S.draft || {};
		return h("form", { class: "screen", novalidate: true, onsubmit: submitForm }, [
			h("h1", {}, "Your details"),
			field("first_name", "First name", { value: draft.first_name || "", required: true, maxlength: 140 }),
			field("last_name", "Last name (optional)", { value: draft.last_name || "", maxlength: 140 }),
			h("fieldset", { class: "field" }, [
				h("legend", {}, "Gender"),
				h(
					"div",
					{ class: "choices" },
					GENDERS.map(function (name) {
						return h("label", { class: "choice" }, [
							h("input", { type: "radio", name: "sex", value: name, checked: gender === name, required: true }),
							h("span", {}, name),
						]);
					})
				),
			]),
			field("dob", "Date of birth", { type: "date", value: draft.dob || "", required: true }),
			field("mobile", "Mobile number", {
				type: "tel",
				inputmode: "numeric",
				value: draft.mobile || "",
				required: true,
				placeholder: "10 digit number",
			}),
			field("email", "Email (optional)", { type: "email", value: draft.email || "" }),
			h("div", { class: "actions" }, [
				button("Back", function () {
					S.draft = null;
					reset();
				}, "secondary"),
				h("button", { type: "submit", class: "btn primary" }, "Register"),
			]),
		]);
	}

	function submitForm(event) {
		event.preventDefault();
		var checked = document.querySelector("input[name=sex]:checked");
		var details = {
			first_name: value("first_name"),
			last_name: value("last_name"),
			sex: checked ? checked.value : "",
			dob: value("dob"),
			mobile: value("mobile"),
			email: value("email"),
		};
		S.draft = details;
		run(call(KIOSK, "register_patient", details), function (patient) {
			S.patient = patient;
			S.mobile = details.mobile;
			S.draft = null;
			go("result");
		});
	}

	function result() {
		var p = S.patient;
		var actions = [button("Print my token", printSlip)];
		if (BOOT.enable_appointment) actions.push(button("Book my appointment", bookAppointment, "secondary"));
		if (BOOT.enable_abha && !p.already_registered) actions.push(button("Create or link my ABHA", function () {
			go("abhaMethod");
		}, "secondary"));
		actions.push(button("Done", reset, "secondary"));

		return h("div", { class: "screen" }, [
			h("h1", {}, p.already_registered ? "Welcome back" : "You are registered"),
			h("p", { class: "token-label" }, "Your token"),
			h("p", { class: "token" }, String(p.token_number)),
			h("dl", { class: "facts" }, [
				h("dt", {}, "Name"),
				h("dd", {}, p.patient_name),
				h("dt", {}, "UHID"),
				h("dd", {}, p.uhid),
				h("dt", {}, "Mobile"),
				h("dd", {}, p.masked_mobile),
			]),
			S.notice ? h("p", { class: "notice" }, S.notice) : null,
			h("div", { class: "actions stack" }, actions),
		]);
	}

	function printSlip() {
		run(call(KIOSK, "get_token_slip", { token: S.patient.token }), function (slip) {
			var frame = h("iframe", { class: "print-frame", title: "Token slip" });
			frame.addEventListener("load", function () {
				frame.contentWindow.focus();
				frame.contentWindow.print();
				setTimeout(function () {
					frame.remove();
				}, 60000);
			});
			document.body.appendChild(frame);
			frame.srcdoc = slip.html;
			S.notice = "Your token is printing.";
			render();
		});
	}

	function bookAppointment() {
		run(call(KIOSK, "book_appointment", { patient: S.patient.uhid }), function (appointment) {
			S.appointment = appointment;
			go("booked");
		});
	}

	function booked() {
		var a = S.appointment;
		return h("div", { class: "screen" }, [
			h("h1", {}, "Appointment booked"),
			h("dl", { class: "facts" }, [
				h("dt", {}, "Date"),
				h("dd", {}, a.date),
				a.time ? h("dt", {}, "Time") : null,
				a.time ? h("dd", {}, a.time) : null,
				h("dt", {}, "Token"),
				h("dd", {}, String(a.token_number)),
			]),
			h("div", { class: "actions" }, [
				button("Back", function () {
					go("result");
				}, "secondary"),
				button("Done", reset),
			]),
		]);
	}

	// ----------------------------------------------------------------------------------------------
	// ABHA: create by Aadhaar OTP, create by mobile OTP, or link an ABHA the patient already has
	// ----------------------------------------------------------------------------------------------

	function abhaMethod() {
		return h("div", { class: "screen" }, [
			h("h1", {}, "ABHA"),
			h("p", { class: "lead" }, "Choose how you would like to continue."),
			h("div", { class: "actions stack" }, [
				button("Create ABHA with Aadhaar", function () {
					S.flow = "aadhaar";
					go("abhaAadhaar");
				}),
				button("Create ABHA with mobile number", function () {
					S.flow = "mobile";
					go("abhaAadhaar");
				}, "secondary"),
				button("I already have an ABHA", function () {
					S.flow = "link";
					go("abhaAadhaar");
				}, "secondary"),
				button("Back", function () {
					go("result");
				}, "secondary"),
			]),
		]);
	}

	// First input of every flow: Aadhaar number, mobile number or ABHA address
	function abhaAadhaar() {
		var cfg = {
			aadhaar: ["Aadhaar number", { inputmode: "numeric", maxlength: 14, placeholder: "12 digit number" }],
			mobile: ["Mobile number", { type: "tel", inputmode: "numeric", value: S.mobile }],
			link: ["ABHA address", { placeholder: "name@abdm" }],
		}[S.flow];
		return h("form", { class: "screen", novalidate: true, onsubmit: sendFirstOtp }, [
			h("h1", {}, S.flow === "link" ? "Link your ABHA" : "Create your ABHA"),
			field("abha_first", cfg[0], Object.assign({ required: true }, cfg[1])),
			h("p", { class: "hint" }, "We will send an OTP to the mobile number linked with it."),
			h("div", { class: "actions" }, [
				button("Back", function () {
					go("abhaMethod");
				}, "secondary"),
				h("button", { type: "submit", class: "btn primary" }, "Send OTP"),
			]),
		]);
	}

	function sendFirstOtp(event) {
		event.preventDefault();
		var input = value("abha_first");
		var p = S.patient.uhid;
		var request = {
			aadhaar: function () {
				return call(ABHA, "send_aadhaar_otp", { patient: p, aadhaar: input });
			},
			mobile: function () {
				S.mobile = input;
				return call(ABHA, "send_mobile_otp_create", { patient: p, mobile: input });
			},
			link: function () {
				return call(ABHA, "send_link_otp", { patient: p, abha_address: input });
			},
		}[S.flow]();
		run(request, function (sent) {
			S.txn = sent.txn_id;
			S.stage = "first";
			go("abhaOtp");
		});
	}

	function abhaOtp() {
		var mobileStage = S.stage === "mobile";
		return h("form", { class: "screen", novalidate: true, onsubmit: verifyOtp }, [
			h("h1", {}, "Enter the OTP"),
			field("otp", "6 digit OTP", { inputmode: "numeric", maxlength: 6, required: true }),
			S.flow === "aadhaar" && S.stage === "first"
				? field("abha_mobile", "Your mobile number", { type: "tel", inputmode: "numeric", value: S.mobile, required: true })
				: null,
			mobileStage ? h("p", { class: "hint" }, "This OTP was sent to your mobile number.") : null,
			h("div", { class: "actions" }, [
				button("Back", function () {
					go("abhaMethod");
				}, "secondary"),
				h("button", { type: "submit", class: "btn primary" }, "Verify"),
			]),
		]);
	}

	function verifyOtp(event) {
		event.preventDefault();
		var otp = value("otp");
		var p = S.patient.uhid;
		var t = S.txn;

		if (S.flow === "link") {
			return run(call(ABHA, "verify_link_otp", { patient: p, txn_id: t, otp: otp }), function (linked) {
				S.abha = linked;
				go("abhaDone");
			});
		}
		if (S.stage === "mobile") {
			return run(call(ABHA, "verify_enrol_mobile_otp", { patient: p, txn_id: t, otp: otp }), loadSuggestions);
		}
		if (S.flow === "mobile") {
			return run(call(ABHA, "verify_mobile_otp_create", { patient: p, txn_id: t, otp: otp }), function (verified) {
				S.txn = verified.txn_id;
				loadSuggestions();
			});
		}
		S.mobile = value("abha_mobile") || S.mobile;
		run(call(ABHA, "verify_aadhaar_otp", { patient: p, txn_id: t, otp: otp, mobile: S.mobile }), function (created) {
			S.abha = created;
			S.txn = created.txn_id || t;
			if (created.mobile_linked) return loadSuggestions();
			// The mobile given is not the Aadhaar-linked one: ABDM sends a second OTP to verify it
			run(call(ABHA, "send_enrol_mobile_otp", { patient: p, txn_id: S.txn, mobile: S.mobile }), function (sent) {
				S.txn = sent.txn_id;
				S.stage = "mobile";
				go("abhaOtp");
			});
		});
	}

	function loadSuggestions() {
		busy = false;
		run(call(ABHA, "get_address_suggestions", { patient: S.patient.uhid }), function (found) {
			S.suggestions = found.suggestions || [];
			go("abhaAddress");
		});
	}

	function abhaAddress() {
		return h("div", { class: "screen" }, [
			h("h1", {}, "Choose your ABHA address"),
			S.suggestions.length
				? h(
						"div",
						{ class: "actions stack" },
						S.suggestions.map(function (address) {
							return button(address, function () {
								chooseAddress(address);
							}, "secondary");
						})
				  )
				: h("p", { class: "lead" }, "No suggestions are available. You can set your address later at the desk."),
			button("Skip for now", function () {
				S.abha = Object.assign({}, S.abha, { status: "created" });
				go("abhaDone");
			}, "secondary"),
		]);
	}

	function chooseAddress(address) {
		run(call(ABHA, "choose_address", { patient: S.patient.uhid, abha_address: address }), function (saved) {
			S.abha = Object.assign({}, S.abha, saved);
			go("abhaDone");
		});
	}

	function abhaDone() {
		var a = S.abha || {};
		return h("div", { class: "screen" }, [
			h("h1", {}, S.flow === "link" ? "Your ABHA is linked" : "Your ABHA is ready"),
			h("dl", { class: "facts" }, [
				a.masked_abha_number ? h("dt", {}, "ABHA number") : null,
				a.masked_abha_number ? h("dd", {}, a.masked_abha_number) : null,
				a.masked_abha_address ? h("dt", {}, "ABHA address") : null,
				a.masked_abha_address ? h("dd", {}, a.masked_abha_address) : null,
			]),
			h("div", { class: "actions" }, [
				button("Back to my token", function () {
					go("result");
				}, "secondary"),
				button("Done", reset),
			]),
		]);
	}

	render();
})();
